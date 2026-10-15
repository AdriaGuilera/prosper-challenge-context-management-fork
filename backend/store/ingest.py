#
# Ingest: catalog bundle -> normalised Postgres tables, in one transaction.
#
#   python -m store.ingest given            # one bundle
#   python -m store.ingest --all            # every bundle under catalogs/
#   python -m store.ingest given --anchor today   # calendar starts this week (live demo)
#
# The payload is never modified. Every row carries catalog_version; re-ingesting the
# same bundle replaces its rows, so ingest is idempotent.
#

import argparse
import asyncio
from collections import Counter
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from . import calendar, embeddings
from .bundle import Bundle, list_bundles, load_bundle, normalize
from .db import apply_schema, dsn
from .hours import HoursError, parse_hours

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

STRUCTURAL = [
    "locations", "location_capabilities", "location_hours", "providers", "provider_languages",
    "provider_locations", "appointment_types", "provider_appointment_types",
    "type_embeddings", "patients", "referrals", "schedules", "appointments",
]


def search_name(name: str) -> str:
    """'Dr. Maria Garcia' -> 'maria garcia': the honorific never helps a fuzzy match."""
    n = normalize(name)
    return n[3:] if n.startswith("dr ") else n


def embed_texts(bundle: Bundle) -> list:
    """(type_id, text) rows: every type name. The type search shortlists on these."""
    return [(t["id"], t["name"]) for t in bundle.catalog["appointment_types"]]


def check(bundle: Bundle) -> None:
    """Refuse a payload whose references don't resolve; everything else loads as given."""
    cat, problems = bundle.catalog, []
    for kind in ("locations", "providers", "appointment_types"):
        problems += [f"duplicate {kind} id {i}" for i, n in Counter(x["id"] for x in cat[kind]).items() if n > 1]
    for p in cat["providers"]:
        problems += [f"{p['id']} works at unknown location {l}" for l in p["location_ids"] if l not in bundle.locations]
        problems += [f"{p['id']} offers unknown type {t}" for t in p["appointment_type_ids"] if t not in bundle.types]
    ids_by_name = bundle.type_ids_by_name()
    problems += [f"patient {p['id']} has a referral for unknown type '{r['type']}'"
                 for p in bundle.patients for r in p.get("referrals", []) if normalize(r["type"]) not in ids_by_name]
    if problems:
        raise ValueError(f"{bundle.name}: " + "; ".join(problems))


async def ingest(conn, bundle: Bundle, anchor: date = None, embed: bool = True) -> dict:
    check(bundle)
    cv = bundle.version
    cat = bundle.catalog
    anchor = anchor or bundle.anchor
    ids_by_name = bundle.type_ids_by_name()
    gz = {normalize(p["name"]): p for p in bundle.gazetteer["places"]}

    async with conn.transaction():
        old = await conn.fetchval("SELECT catalog_version FROM catalogs WHERE name = $1", bundle.name)
        for v in {cv, old} - {None}:
            for table in STRUCTURAL:
                await conn.execute(f"DELETE FROM {table} WHERE catalog_version = $1", v)
        await conn.execute("DELETE FROM catalogs WHERE name = $1 OR catalog_version = $2", bundle.name, cv)

        # ---- gazetteer (shared) -------------------------------------------------
        await conn.execute("DELETE FROM gazetteer_places")
        await conn.executemany(
            """INSERT INTO gazetteer_places (name, kind, aliases, search_names, geom)
               VALUES ($1, $2, $3, $4, ST_GeomFromText($5, 0))""",
            [
                (p["name"], p["kind"], p["aliases"],
                 [normalize(p["name"])] + [normalize(a) for a in p["aliases"]],
                 "MULTIPOINT(" + ",".join(f"({x} {y})" for x, y in p["points"]) + ")")
                for p in bundle.gazetteer["places"]
            ],
        )

        # ---- locations ----------------------------------------------------------
        loc_rows, hour_rows = [], []
        for l in cat["locations"]:
            m = bundle.places.get(l["id"])
            place = gz.get(normalize(m["place"])) if m else None
            xy = (m.get("at") if m else None) or (place["points"][0] if place else None)
            loc_rows.append((cv, l["id"], l["name"], l["address"], l["city"], l["phone"], l["hours"],
                             place["name"] if place else None,
                             f"POINT({xy[0]} {xy[1]})" if xy else None))
            try:
                for d, intervals in parse_hours(l["hours"]).items():
                    for a, b in intervals:
                        hour_rows.append((cv, l["id"], d + 1, a, b))
            except HoursError:
                pass  # unparseable hours: the location gets no open days (calendar.py)
        await conn.executemany(
            """INSERT INTO locations (catalog_version, id, name, address, city, phone, hours_text, place, geom)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8, ST_GeomFromText($9, 0))""", loc_rows)
        await conn.executemany(
            "INSERT INTO location_hours VALUES ($1,$2,$3,$4,$5)", hour_rows)
        await conn.executemany(
            "INSERT INTO location_capabilities VALUES ($1,$2,$3)",
            [(cv, l["id"], c) for l in cat["locations"] for c in sorted(set(l.get("capabilities", [])))])

        # ---- providers ----------------------------------------------------------
        await conn.executemany(
            "INSERT INTO providers VALUES ($1,$2,$3,$4,$5,$6,$7)",
            [(cv, p["id"], p["name"], search_name(p["name"]), p["title"], p["specialty"],
              p["accepting_new_patients"]) for p in cat["providers"]])
        await conn.executemany(
            "INSERT INTO provider_languages VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            [(cv, p["id"], lang) for p in cat["providers"] for lang in p.get("languages", [])])
        await conn.executemany(
            "INSERT INTO provider_locations VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            [(cv, p["id"], lid) for p in cat["providers"] for lid in p["location_ids"]])
        await conn.executemany(
            "INSERT INTO provider_appointment_types VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
            [(cv, p["id"], tid) for p in cat["providers"] for tid in p["appointment_type_ids"]])

        # ---- types + meaning layer ---------------------------------------------------
        await conn.executemany(
            "INSERT INTO appointment_types VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            [(cv, t["id"], t["name"], t["specialty"], t["duration_min"], t["requires_referral"],
              t["new_patients_allowed"], t.get("required_capability") or None)
             for t in cat["appointment_types"]])


        if embed:
            texts = embed_texts(bundle)
            vectors = await embeddings.embed(conn, [t for _, t in texts])
            await conn.executemany(
                "INSERT INTO type_embeddings VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
                [(cv, tid, text, embeddings.MODEL, vectors[text]) for tid, text in texts])

        # ---- patients --------------------------------------------------------------
        await conn.executemany(
            "INSERT INTO patients VALUES ($1,'base',$2,$3,$4,$5)",
            [(cv, p["id"], p["name"], search_name(p["name"]), date.fromisoformat(p["dob"])) for p in bundle.patients])
        await conn.executemany(
            "INSERT INTO referrals VALUES ($1,'base',$2,$3,$4) ON CONFLICT DO NOTHING",
            [(cv, p["id"], tid, date.fromisoformat(r["expires"]))
             for p in bundle.patients for r in p.get("referrals", [])
             for tid in ids_by_name.get(normalize(r["type"]), [])])

        # ---- calendar ----------------------------------------------------------------
        cal = bundle.calendar
        schedules = calendar.generate_schedules(cat, bundle.seed, cal.get("density", "normal"))
        await conn.executemany("INSERT INTO schedules VALUES ($1,$2,$3,$4,$5,$6)",
                               [(cv, *row) for row in schedules])
        prebooked = calendar.generate_prebookings(cat, schedules, bundle.seed, anchor, cal.get("prebook_rate", 0.3))
        await conn.executemany(
            """INSERT INTO appointments (catalog_version, scope, confirmation, idempotency_key, patient_id,
                   provider_id, location_id, appointment_type_id, starts_at, ends_at)
               VALUES ($1, 'base', $2, $2, 'pat_mock', $3, $4, $5, $6, $7)""",
            [(cv, f"PRE-{i:05d}", *row) for i, row in enumerate(prebooked)])

        stats = {
            "locations": len(cat["locations"]), "providers": len(cat["providers"]),
            "appointment_types": len(cat["appointment_types"]),
            "schedules": len(schedules), "prebooked": len(prebooked),
            "embedded_texts": len(embed_texts(bundle)) if embed else 0,
        }
        await conn.execute(
            "INSERT INTO catalogs (name, catalog_version, source_path, calendar_seed, calendar_anchor) VALUES ($1,$2,$3,$4,$5)",
            bundle.name, cv, str(bundle.path), bundle.seed, anchor)
    return stats


def _anchor(arg: str) -> date:
    if arg == "today":
        today = date.today()
        return today - timedelta(days=today.weekday())  # Monday of this week
    return date.fromisoformat(arg)


async def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundles", nargs="*", help="bundle names under backend/catalogs/")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--anchor", help="calendar start: YYYY-MM-DD or 'today' (defaults to the bundle's)")
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args(argv)
    names = list_bundles() if args.all else (args.bundles or ["given"])
    conn = await asyncpg.connect(dsn())
    await apply_schema(conn)
    from pgvector.asyncpg import register_vector
    await register_vector(conn)
    try:
        for name in names:
            bundle = load_bundle(name)
            started = datetime.now()
            stats = await ingest(conn, bundle, _anchor(args.anchor) if args.anchor else None, not args.no_embed)
            ms = (datetime.now() - started).total_seconds() * 1000
            print(f"[{name}] catalog_version={bundle.version} ingested in {ms:.0f} ms  {stats}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
