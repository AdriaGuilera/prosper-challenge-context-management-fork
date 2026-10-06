#
# Ingest lint: what is wrong or risky in a payload, reported instead of silently
# passed through. Pure Python over the bundle; ingest stores the report with the
# catalog and prints it.
#

from collections import Counter, defaultdict
from itertools import combinations

from .bundle import Bundle, normalize
from .hours import HoursError, parse_hours
from .policies import orphan_types, unbookable_types


def _surname(name: str) -> str:
    parts = normalize(name.replace("Dr.", "")).split()
    return parts[-1] if parts else ""


def _policy_columns(t: dict) -> tuple:
    return (t["specialty"], t["duration_min"], t["requires_referral"], t["new_patients_allowed"],
            t.get("required_capability"))


def lint(bundle: Bundle, similarities: dict = None) -> dict:
    """similarities: optional {(type_a, type_b): cosine} from the embedding pass."""
    cat = bundle.catalog
    types = bundle.types
    locations = bundle.locations
    findings: dict = defaultdict(list)

    # ---- invariants -----------------------------------------------------------
    for kind in ("locations", "providers", "appointment_types"):
        dupes = [i for i, n in Counter(x["id"] for x in cat[kind]).items() if n > 1]
        for d in dupes:
            findings["invariant_violations"].append(f"duplicate {kind} id {d}")
    caps_present = {c for l in cat["locations"] for c in l.get("capabilities", [])}
    for p in cat["providers"]:
        for lid in p["location_ids"]:
            if lid not in locations:
                findings["invariant_violations"].append(f"{p['id']} practises at unknown location {lid}")
        for tid in p["appointment_type_ids"]:
            if tid not in types:
                findings["invariant_violations"].append(f"{p['id']} offers unknown type {tid}")
        if not p["location_ids"]:
            findings["invariant_violations"].append(f"{p['id']} has no location")
    for t in cat["appointment_types"]:
        cap = t.get("required_capability")
        if cap is not None and cap not in caps_present:
            findings["invariant_violations"].append(f"{t['id']} needs '{cap}', which no location has")
        if "required_capability" in t and t["required_capability"] in ("", None):
            findings["invariant_violations"].append(f"{t['id']} carries an empty required_capability")

    # ---- reachability -----------------------------------------------------------
    findings["orphan_types"] = [f"{tid} {types[tid]['name']}" for tid in orphan_types(cat)]
    findings["unbookable_types"] = [f"{tid} {types[tid]['name']}" for tid in unbookable_types(cat) if tid in types]

    # ---- collisions ---------------------------------------------------------------
    by_name = defaultdict(list)
    by_surname = defaultdict(list)
    for p in cat["providers"]:
        by_name[normalize(p["name"])].append(p["id"])
        by_surname[_surname(p["name"])].append(p["id"])
    findings["provider_name_collisions"] = [
        f"{n}: {', '.join(ids)}" for n, ids in sorted(by_name.items()) if len(ids) > 1
    ]
    findings["provider_surname_collisions"] = [
        f"{s}: {len(ids)} providers" for s, ids in sorted(by_surname.items()) if len(ids) >= 3
    ]
    type_names = Counter(normalize(t["name"]) for t in cat["appointment_types"])
    findings["type_name_collisions"] = [n for n, c in type_names.items() if c > 1]

    # ---- near-duplicate types --------------------------------------------------------
    registered = []
    ids_by_name = bundle.type_ids_by_name()
    for c in bundle.clusters:
        registered.append({i for m in c["members"] for i in ids_by_name.get(normalize(m), [])})
    near = []
    for a, b in combinations(cat["appointment_types"], 2):
        same_cols = _policy_columns(a) == _policy_columns(b)
        sim = (similarities or {}).get((a["id"], b["id"])) or (similarities or {}).get((b["id"], a["id"]))
        if same_cols or (sim is not None and sim >= 0.70):
            covered = any({a["id"], b["id"]} <= r for r in registered)
            near.append({
                "pair": [a["name"], b["name"]],
                "identical_columns": same_cols,
                "cosine": round(sim, 3) if sim is not None else None,
                "registered_cluster": covered,
            })
    # identical columns alone is common (whole specialty blocks); keep the ones that are
    # also lexically or semantically close, which is where a caller's words land on both
    findings["near_duplicate_types"] = [
        n for n in near
        if not n["identical_columns"] or (n["cosine"] or 0) >= 0.55 or n["registered_cluster"]
    ]
    findings["unregistered_near_duplicates"] = [
        n["pair"] for n in findings["near_duplicate_types"] if not n["registered_cluster"] and (n["cosine"] or 0) >= 0.70
    ]

    # ---- hours -------------------------------------------------------------------------
    for l in cat["locations"]:
        try:
            parse_hours(l["hours"])
        except HoursError as e:
            findings["unparseable_hours"].append(f"{l['id']} '{l['hours']}': {e}")

    # ---- derived artifacts -------------------------------------------------------------
    gz_names = {normalize(p["name"]) for p in bundle.gazetteer.get("places", [])}
    for l in cat["locations"]:
        m = bundle.places.get(l["id"])
        if not m:
            findings["locations_without_place"].append(l["id"])
        elif normalize(m["place"]) not in gz_names:
            findings["locations_without_place"].append(f"{l['id']} -> unknown place '{m['place']}'")
    lex_names = {normalize(n) for names in bundle.aliases.values() for n in names}
    lex_names |= {normalize(m) for c in bundle.clusters for m in c["members"]}
    findings["lexicon_names_not_in_catalog"] = sorted(n for n in lex_names if n not in ids_by_name)
    findings["types_without_alias"] = sorted(
        t["name"] for t in cat["appointment_types"]
        if normalize(t["name"]) not in lex_names
    )
    for p in bundle.patients:
        for r in p.get("referrals", []):
            if normalize(r["type"]) not in ids_by_name:
                findings["invariant_violations"].append(f"patient {p['id']} referral for unknown type '{r['type']}'")

    counts = {k: len(v) for k, v in findings.items()}
    return {"counts": counts, **findings}
