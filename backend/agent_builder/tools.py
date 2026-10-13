#
# Catalog tools: the lookups a node can expose to the LLM, by name.
#
# A node lists tool names in its `tools` field; AgentBuilder turns each into a
# FlowsFunctionSchema that answers WITHOUT a transition. Every tool runs over the
# Postgres store (decision 013), asynchronously (decision 011), and returns a
# bounded, answer-shaped result (decision 008):
#
#   {total, shown, truncated, options[], ...}   never a bare row array, never a silent cut
#
# The model never sets a limit, never passes patient state it could get wrong, and
# never authors an idempotency key or a catalog version: the server owns all four.
#

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, Optional

from store import db
from store.bundle import GAZETTEER_PATH, load_bundle, normalize
from store.geo import resolve_place
from store.hours import DAY_NAMES, summarize
from store.policies import Candidate, Conversation, PatientState, PolicySet
from store.search import rank_types
from store.calendar import LEAD
from store.slots import free_slots, spoken

MAX_RESULTS = 5
MAX_SLOT_OPTIONS = 3
_GZ = json.loads(GAZETTEER_PATH.read_text())
NEAR_KM, FAR_KM = _GZ["near_km"], _GZ["far_km"]


@dataclass
class ToolContext:
    """Per-call state: which catalog, which calendar scope, what time it is, and what
    this conversation has already established (patient, slots offered)."""

    catalog: str = "given"
    scope: str = "live"                                  # bookings land here; 'base' is shared
    clock: Callable[[], datetime] = datetime.now
    use_vectors: bool = True
    session: dict = field(default_factory=dict)
    cv: Optional[str] = None
    enums: dict = field(default_factory=dict)
    policies: Optional[PolicySet] = None                 # every "can this be booked" decision goes through this

    async def prepare(self) -> None:
        if self.cv:
            return
        pool = await db.pool()
        async with pool.acquire() as conn:
            self.cv = await conn.fetchval("SELECT catalog_version FROM catalogs WHERE name = $1", self.catalog)
            if not self.cv:
                raise RuntimeError(f"Catalog '{self.catalog}' is not ingested. Run `make ingest`.")
            bundle = load_bundle(self.catalog)
            if bundle.version != self.cv:
                raise RuntimeError(f"Catalog '{self.catalog}' changed on disk since ingest. Run `make ingest`.")
            self.policies = PolicySet(bundle.catalog)
            self.enums = {
                "specialty": [r[0] for r in await conn.fetch(
                    "SELECT DISTINCT specialty FROM (SELECT specialty FROM providers WHERE catalog_version = $1 "
                    "UNION SELECT specialty FROM appointment_types WHERE catalog_version = $1) s ORDER BY 1", self.cv)],
                "capability": [r[0] for r in await conn.fetch(
                    "SELECT DISTINCT capability FROM location_capabilities WHERE catalog_version = $1 ORDER BY 1", self.cv)],
                "language": [r[0] for r in await conn.fetch(
                    "SELECT DISTINCT language FROM provider_languages WHERE catalog_version = $1 ORDER BY 1", self.cv)],
            }

    @property
    def scopes(self) -> list:
        return ["base", self.scope]

    def now(self) -> datetime:
        return self.clock().replace(second=0, microsecond=0)


def _bounded(options: list, total: Optional[int] = None, limit: int = MAX_RESULTS, **extra) -> dict:
    total = len(options) if total is None else total
    shown = options[:limit]
    return {"total": total, "shown": len(shown), "truncated": total > len(shown), **extra, "options": shown}


def _today(ctx: ToolContext) -> str:
    return ctx.now().strftime("%A %B %-d, %Y")


# ---- patients ---------------------------------------------------------------

_DOB_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%B %d, %Y", "%m-%d-%Y"]


def parse_dob(text: str) -> Optional[date]:
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", (text or "").strip())
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


async def lookup_patient(args: dict, ctx: ToolContext, conn) -> dict:
    name = (args.get("full_name") or "").strip()
    dob = parse_dob(args.get("date_of_birth", ""))
    if not name or not dob:
        return {"error": "Need the caller's full name and a date of birth with day, month and year."}
    q = normalize(name)
    rows = await conn.fetch(
        """SELECT id, full_name, scope, name_score($3, search_name) AS sim FROM patients
           WHERE catalog_version = $1 AND scope = ANY($2) AND date_of_birth = $4
             AND name_score($3, search_name) >= 0.75
           ORDER BY sim DESC LIMIT 2""",
        ctx.cv, ctx.scopes, q, dob)
    today = ctx.now().date()
    if not rows:
        ctx.session["patient"] = {"id": None, "name": name, "dob": dob.isoformat(), "is_new": True, "referrals": []}
        return {
            "found": False, "patient_status": "new", "age": _age(dob, today), "today": _today(ctx),
            "note": "No record under that name and date of birth, so treat them as a NEW patient. "
                    "If they say they've been here before, check the spelling of their name once.",
        }
    p = rows[0]
    refs = await conn.fetch(
        """SELECT r.appointment_type_id, t.name, r.expires_on FROM referrals r
           JOIN appointment_types t ON t.catalog_version = r.catalog_version AND t.id = r.appointment_type_id
           WHERE r.catalog_version = $1 AND r.scope = ANY($2) AND r.patient_id = $3 AND r.expires_on >= $4
           ORDER BY t.name""",
        ctx.cv, ctx.scopes, p["id"], today)
    ctx.session["patient"] = {"id": p["id"], "name": p["full_name"], "dob": dob.isoformat(), "is_new": False,
                              "referrals": [r["appointment_type_id"] for r in refs]}
    return {
        "found": True, "patient_status": "existing", "name": p["full_name"], "age": _age(dob, today),
        "today": _today(ctx), "referrals_on_file": [r["name"] for r in refs],
    }


def _state(patient: Optional[dict]) -> Optional[PatientState]:
    """The looked-up patient as the policies see them."""
    if not patient:
        return None
    return PatientState(is_new=patient["is_new"], referral_type_ids=frozenset(patient["referrals"]))


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# ---- appointment types ----------------------------------------------------------


async def _type_rows(conn, ctx: ToolContext, ids: list) -> dict:
    rows = await conn.fetch("SELECT t.* FROM appointment_types t WHERE t.catalog_version = $1 AND t.id = ANY($2)", ctx.cv, ids)
    offered = {t for _, _, t in ctx.policies.triples()}
    return {r["id"]: {**dict(r), "offered": r["id"] in offered} for r in rows}


def _patient_block(ctx: ToolContext, t, patient: Optional[dict]) -> Optional[str]:
    """The type-level policies (4, 5a) for this patient, as the reason they block it."""
    v = ctx.policies.violations(Candidate(appointment_type_id=t["id"]), _state(patient)) if patient else []
    return " ".join(x.reason for x in v) or None


def _type_option(ctx: ToolContext, t, hint: Optional[str], patient: Optional[dict]) -> dict:
    opt = {
        "appointment_type_id": t["id"], "name": t["name"], "specialty": t["specialty"],
        "duration_min": t["duration_min"], "needs_referral": t["requires_referral"],
        "open_to_new_patients": t["new_patients_allowed"],
        "needs_capability": t["required_capability"], "offered_here": t["offered"],
    }
    if hint:
        opt["pick_if_caller_wants"] = hint
    block = _patient_block(ctx, t, patient)
    if block:
        opt["blocked_for_this_patient"] = block
    if not t["offered"]:
        opt["note"] = "No provider here offers this, so it cannot be booked."
    return opt


async def search_appointment_types(args: dict, ctx: ToolContext, conn) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "Pass the caller's own words as query."}
    specialty, provider_id, location_id = args.get("specialty"), args.get("provider_id"), args.get("location_id")
    # narrowed to a provider or site: only the types the catalog policies (1-3) allow there
    allowed = sorted({t for _, _, t in ctx.policies.triples(provider_id=provider_id, location_id=location_id)}) \
        if (provider_id or location_id) else None
    candidates = [r["id"] for r in await conn.fetch(
        """SELECT t.id FROM appointment_types t WHERE t.catalog_version = $1
             AND ($2::text IS NULL OR t.specialty = $2) AND ($3::text[] IS NULL OR t.id = ANY($3))""",
        ctx.cv, specialty, allowed)]
    ranking = await rank_types(conn, ctx.cv, query, candidates, ctx.use_vectors)
    if ranking.status == "none":
        return _bounded([], status="no_match",
                        instruction="Nothing matched. Ask the caller to describe the visit differently, "
                                    "or say what kinds of visits are available in a specialty.")
    patient = ctx.session.get("patient")
    minor = bool(patient) and _age(date.fromisoformat(patient["dob"]), ctx.now().date()) < 18
    if minor and not specialty and ranking.status in ("match", "ask", "unsure"):
        # The patient is a child. Specialty is never an eligibility key, but it is a fair
        # ranking signal: if the words landed on adult visits, rank inside pediatrics too.
        top = await _type_rows(conn, ctx, list(ranking.candidates) or [t for t, _, _ in ranking.ranked[:1]])
        if not any(r["specialty"] == "Pediatrics" for r in top.values()):
            peds = [r["id"] for r in await conn.fetch(
                "SELECT id FROM appointment_types WHERE catalog_version = $1 AND specialty = 'Pediatrics' AND id = ANY($2)",
                ctx.cv, candidates)]
            child = await rank_types(conn, ctx.cv, query, peds, ctx.use_vectors) if peds else None
            if child and child.ranked:
                child_rows = await _type_rows(conn, ctx, [t for t, _, _ in child.ranked[:3]])
                options = [_type_option(ctx, child_rows[t], None, patient) for t, _, _ in child.ranked[:3] if t in child_rows]
                return {"status": "ask", **_bounded(options, total=len(child.ranked)),
                        "choose_between": [o["name"] for o in options[:2]],
                        "instruction": "The patient is a child, so these pediatric visits are the likely fit, not the adult "
                                       "ones the words also match. Ask a plain question to pick between them."}
    hints = json.loads(ranking.cluster["member_hints"]) if ranking.cluster else {}
    first = list(ranking.candidates) if ranking.status in ("ask", "match") else []
    order = first + [tid for tid, _, _ in ranking.ranked if tid not in first]
    rows = await _type_rows(conn, ctx, order[:MAX_RESULTS])
    options = [_type_option(ctx, rows[tid], hints.get(tid), patient) for tid in order[:MAX_RESULTS] if tid in rows]
    out = _bounded(options, total=len(ranking.ranked))
    if ranking.status in ("ask", "match") and all(not rows[c]["offered"] for c in ranking.candidates if c in rows):
        names = [rows[c]["name"] for c in ranking.candidates if c in rows]
        return {"status": "not_offered", **out, "not_offered": names,
                "instruction": "The clinic doesn't offer this: no provider here does it. Say so plainly. "
                               "Don't offer a different visit as if it were the same thing."}
    if ranking.status == "match":
        out = {"status": "match", **out, "instruction": "The first option is the match. Confirm it in plain words."}
    elif ranking.status == "ask":
        names = [rows[t]["name"] for t in ranking.candidates if t in rows]
        default = ranking.cluster["safer_default"] if ranking.cluster else None
        out = {
            "status": "ask", **out, "choose_between": names,
            "question": ranking.cluster["question"] if ranking.cluster else None,
            "safer_default": rows[default]["name"] if default in rows else None,
            "instruction": "These are easy to confuse. Ask the question (in the caller's words, not the catalog's) "
                           "and let their answer pick, using pick_if_caller_wants. Do not choose for them. If they "
                           "truly can't say, propose the safer_default and get a clear yes.",
        }
    else:
        out = {"status": "unsure", **out,
               "instruction": "Low confidence. Ask the caller what the visit is for in other words, "
                              "or offer the closest one or two options as a question."}
    return out


# ---- providers ------------------------------------------------------------------

_HONORIFIC = re.compile(r"^(dr|doctor|doc)\s+")


async def find_providers(args: dict, ctx: ToolContext, conn) -> dict:
    q = _HONORIFIC.sub("", normalize(args.get("name") or ""))
    specialty = args.get("specialty")
    type_id = args.get("appointment_type_id")
    location_id = args.get("location_id")
    language = args.get("language")
    accepting = args.get("accepting_new_patients")
    if not (q or specialty or type_id or location_id or language):
        return {"error": "Give at least one of name, specialty, appointment_type_id, location_id or language."}
    # providers the catalog policies (1-3) allow for this type (at this site, if given)
    can_do = sorted({p for p, _, _ in ctx.policies.triples(location_id=location_id, type_id=type_id)}) if type_id else None
    rows = await conn.fetch(
        """SELECT p.id, p.name, p.title, p.specialty, p.accepting_new_patients, p.search_name,
                  CASE WHEN $2 = '' THEN 1.0 ELSE name_score($2, p.search_name) END AS score,
                  (SELECT json_agg(json_build_object('location_id', l.id, 'name', l.name) ORDER BY l.name)
                     FROM provider_locations pl JOIN locations l
                       ON l.catalog_version = pl.catalog_version AND l.id = pl.location_id
                    WHERE pl.catalog_version = p.catalog_version AND pl.provider_id = p.id) AS locations,
                  (SELECT array_agg(language ORDER BY language) FROM provider_languages pg
                    WHERE pg.catalog_version = p.catalog_version AND pg.provider_id = p.id) AS languages
           FROM providers p
           WHERE p.catalog_version = $1
             AND ($2 = '' OR name_score($2, p.search_name) >= 0.6)
             AND ($3::text IS NULL OR p.specialty = $3)
             AND ($4::text[] IS NULL OR p.id = ANY($4))
             AND ($5::text IS NULL OR EXISTS (SELECT 1 FROM provider_locations pl WHERE pl.catalog_version = $1
                    AND pl.provider_id = p.id AND pl.location_id = $5))
             AND ($6::text IS NULL OR EXISTS (SELECT 1 FROM provider_languages pg WHERE pg.catalog_version = $1
                    AND pg.provider_id = p.id AND lower(pg.language) = lower($6)))
             AND ($7::bool IS NULL OR p.accepting_new_patients = $7)
           ORDER BY score DESC, p.name, p.id""",
        ctx.cv, q, specialty, can_do, location_id, language, accepting)
    if q:
        # exact full name, then exact surname/first-name word, then fuzzy (misheard names)
        exact = [r for r in rows if r["search_name"] == q]
        word = [r for r in rows if q in r["search_name"].split()]
        rows = exact or word or rows
    by_specialty: dict = {}
    for r in rows:
        by_specialty[r["specialty"]] = by_specialty.get(r["specialty"], 0) + 1
    patient = ctx.session.get("patient")
    options = []
    for r in rows:
        opt = {
            "provider_id": r["id"], "name": r["name"], "title": r["title"], "specialty": r["specialty"],
            "accepting_new_patients": r["accepting_new_patients"], "languages": r["languages"] or [],
            "locations": json.loads(r["locations"]) if isinstance(r["locations"], str) else (r["locations"] or []),
        }
        if patient:
            # answer-shaped: whether THIS caller can see them, so "not accepting new patients"
            # is never misread as a refusal for an existing patient
            blocked = ctx.policies.violations(Candidate(provider_id=r["id"]), _state(patient))
            opt["can_see_this_patient"] = not blocked
            if blocked:
                opt["why_not"] = " ".join(v.reason for v in blocked)
        options.append(opt)
    out = _bounded(options, by_specialty=by_specialty)
    if q and len(rows) > 1:
        differs = [k for k in ("specialty", "title", "locations", "languages", "accepting_new_patients")
                   if len({json.dumps(o[k], sort_keys=True) for o in options}) > 1]
        out = {"status": "ask", **out, "distinguish_by": differs,
               "instruction": f"{len(rows)} providers match that name. Ask which one, using what tells them apart "
                              f"({', '.join(differs) or 'their full name'}). Never assume."}
    elif q and len(rows) == 1 and rows[0]["search_name"] != q and q not in rows[0]["search_name"].split():
        out = {"status": "confirm", **out,
               "instruction": "Closest name only, not an exact match. Confirm the name with the caller."}
    elif not rows:
        filtered = (specialty or type_id or location_id or language or accepting is not None)
        exists = await conn.fetch(
            "SELECT name, specialty, accepting_new_patients FROM providers WHERE catalog_version = $1 "
            "AND name_score($2, search_name) >= 0.6 ORDER BY name_score($2, search_name) DESC LIMIT 3",
            ctx.cv, q) if q and filtered else []
        if exists:
            # a filter must never make a real person disappear: say who it is and why they don't fit
            who = "; ".join(f"{r['name']} ({r['specialty']}"
                            + ("" if r["accepting_new_patients"] else ", not accepting new patients") + ")"
                            for r in exists)
            out = {"status": "exists_but_filtered", **out, "named_providers": who,
                   "instruction": f"That provider exists ({who}) but doesn't match what you filtered on. "
                                  "Tell the caller exactly why they can't book with them, then offer alternatives."}
        else:
            out = {"status": "no_match", **out,
                   "instruction": "No provider by that name. Say so; don't guess. Offer to search by specialty."}
    return out


# ---- locations --------------------------------------------------------------------


def _week(rows) -> dict:
    week: dict = {}
    for r in rows:
        week.setdefault(r["weekday"] - 1, []).append((r["opens"], r["closes"]))
    return week


async def _place_point(conn, text: str) -> tuple:
    """(point, problem dict or None)."""
    res = await resolve_place(conn, text)
    if res.status == "resolved":
        return res, None
    if res.status == "ambiguous":
        if res.place:  # a street through several areas
            areas = [c["area"] for c in res.candidates]
            return res, {"status": "ask_place", "place": res.place, "areas": areas,
                         "instruction": f"{res.place} runs through {' and '.join(areas)}. Ask which part they mean."}
        return res, {"status": "ask_place", "candidates": [c["name"] for c in res.candidates],
                     "instruction": "Several places sound like that. Ask which one they mean."}
    return res, {"status": "unknown_place",
                 "instruction": "That place isn't on our map. Ask for a nearby neighbourhood or landmark, or offer the list of locations."}


async def find_locations(args: dict, ctx: ToolContext, conn) -> dict:
    name = normalize(args.get("name") or "")
    near = args.get("near")
    capability = args.get("capability")
    open_on = args.get("open_on")
    type_id = args.get("appointment_type_id")
    provider_id = args.get("provider_id")
    point, place_note = None, None
    if near:
        res, problem = await _place_point(conn, near)
        if problem:
            return {**problem, "total": 0, "shown": 0, "truncated": False, "options": []}
        point = res.point
        if res.confusable_with:
            place_note = f"Heard '{res.place}', which sounds like {', '.join(res.confusable_with)}. Confirm if unsure."
    weekday = (DAY_NAMES.index(open_on) + 1) if open_on in DAY_NAMES else None
    # locations where the catalog policies (1-3) let someone do this type
    hosts = sorted({l for _, l, _ in ctx.policies.triples(type_id=type_id)}) if type_id else None
    rows = await conn.fetch(
        """SELECT l.id, l.name, l.address, l.city, l.phone, l.place,
                  CASE WHEN $2::float8 IS NULL THEN NULL
                       ELSE ST_Distance(l.geom, ST_SetSRID(ST_MakePoint($2, $3), 0)) END AS km,
                  CASE WHEN $8 = '' THEN 1.0 ELSE greatest(similarity(lower(l.name), $8),
                       word_similarity($8, lower(l.name)), similarity(lower(coalesce(l.place, '')), $8)) END AS score,
                  (SELECT array_agg(capability ORDER BY capability) FROM location_capabilities c
                    WHERE c.catalog_version = l.catalog_version AND c.location_id = l.id) AS capabilities
           FROM locations l
           WHERE l.catalog_version = $1
             AND ($4::text IS NULL OR EXISTS (SELECT 1 FROM location_capabilities c WHERE c.catalog_version = $1
                    AND c.location_id = l.id AND c.capability = $4))
             AND ($5::int IS NULL OR EXISTS (SELECT 1 FROM location_hours h WHERE h.catalog_version = $1
                    AND h.location_id = l.id AND h.weekday = $5))
             AND ($6::text[] IS NULL OR l.id = ANY($6))
             AND ($7::text IS NULL OR EXISTS (SELECT 1 FROM provider_locations pl WHERE pl.catalog_version = $1
                    AND pl.location_id = l.id AND pl.provider_id = $7))
             AND ($8 = '' OR greatest(similarity(lower(l.name), $8), word_similarity($8, lower(l.name)),
                    similarity(lower(coalesce(l.place, '')), $8)) >= 0.4)
           ORDER BY CASE WHEN $2::float8 IS NULL THEN 0 ELSE l.geom <-> ST_SetSRID(ST_MakePoint($2, $3), 0) END,
                    score DESC, l.name""",
        ctx.cv, point[0] if point else None, point[1] if point else None, capability, weekday, hosts,
        provider_id, name)
    hours = await conn.fetch(
        "SELECT location_id, weekday, opens, closes FROM location_hours WHERE catalog_version = $1 AND location_id = ANY($2) ORDER BY opens",
        ctx.cv, [r["id"] for r in rows])
    by_loc: dict = {}
    for h in hours:
        by_loc.setdefault(h["location_id"], []).append(h)
    by_capability: dict = {}
    options = []
    for r in rows:
        for c in r["capabilities"] or []:
            by_capability[c] = by_capability.get(c, 0) + 1
        week = _week(by_loc.get(r["id"], []))
        opt = {
            "location_id": r["id"], "name": r["name"], "address": f"{r['address']}, {r['city']}", "phone": r["phone"],
            "hours": summarize(week) if week else "hours unavailable", "capabilities": r["capabilities"] or [],
        }
        if r["km"] is not None:
            opt["distance_km"] = round(r["km"], 1)
        options.append(opt)
    out = _bounded(options, by_capability=by_capability)
    if point is not None and options:
        d0 = options[0]["distance_km"]
        if d0 > FAR_KM:
            out["note"] = f"Nothing near {near}: the closest location is {options[0]['name']}, about {d0:.0f} km away."
        elif len(options) > 1 and abs(options[1]["distance_km"] - d0) < 0.05:
            out["note"] = f"{options[0]['name']} and {options[1]['name']} are equally close to {near}. Offer both."
    if place_note:
        out["place_check"] = place_note
    if open_on and weekday is None:
        out["error"] = "open_on must be a full weekday name like 'Saturday'."
    elif open_on and not options:
        out["note"] = f"No location matching that is open on {open_on}."
    return out


# ---- availability -------------------------------------------------------------------


async def check_availability(args: dict, ctx: ToolContext, conn) -> dict:
    type_id = args.get("appointment_type_id")
    provider_id, location_id, near = args.get("provider_id"), args.get("location_id"), args.get("near")
    t = await conn.fetchrow("SELECT * FROM appointment_types WHERE catalog_version = $1 AND id = $2", ctx.cv, type_id)
    if not t:
        return {"error": f"Unknown appointment type '{type_id}'. Look it up with search_appointment_types first."}
    patient = ctx.session.get("patient")
    state = _state(patient)   # None before lookup: catalog policies only, and a warning below
    pairs = sorted((p, l) for p, l, _ in ctx.policies.triples(state, provider_id, location_id, type_id))
    if not pairs:
        reasons = ctx.policies.explain_none(type_id, provider_id, location_id, state)
        out = {"status": "not_bookable", "reasons": reasons, "total": 0, "shown": 0, "truncated": False, "options": []}
        if provider_id or location_id:
            alt = len(ctx.policies.triples(state, type_id=type_id))
            if alt:
                out["alternatives"] = f"{alt} other provider/location combinations can do {t['name']} for this patient. Offer to look without the preference."
        return out

    now = ctx.now()
    after = args.get("on_or_after")
    if after:
        try:
            now = max(now, datetime.combine(date.fromisoformat(after), datetime.min.time()))
        except ValueError:
            return {"error": "on_or_after must be YYYY-MM-DD."}
    point = None
    if near:
        res, problem = await _place_point(conn, near)
        if problem:
            return {**problem, "total": 0, "shown": 0, "truncated": False, "options": []}
        point = res.point
    pair_list = pairs
    slots = await free_slots(conn, ctx.cv, pair_list, t["duration_min"], max(now, ctx.now() + LEAD), ctx.scopes,
                             args.get("part_of_day") or "any")
    meta = {r["id"]: r for r in await conn.fetch(
        """SELECT l.id, l.name, CASE WHEN $3::float8 IS NULL THEN NULL
                  ELSE ST_Distance(l.geom, ST_SetSRID(ST_MakePoint($3, $4), 0)) END AS km
           FROM locations l WHERE l.catalog_version = $1 AND l.id = ANY($2)""",
        ctx.cv, list({l for _, l in pair_list}), point[0] if point else None, point[1] if point else None)}
    provs = {r["id"]: r for r in await conn.fetch(
        "SELECT id, name, title, specialty FROM providers WHERE catalog_version = $1 AND id = ANY($2)",
        ctx.cv, list({p for p, _ in pair_list}))}
    grouped: dict = {}
    for p, l, s in slots:
        grouped.setdefault((p, l), []).append(s)
    if not grouped:
        return {"status": "no_openings", "total": 0, "shown": 0, "truncated": False, "options": [],
                "instruction": "No openings in the next three weeks for this. Offer a different provider, location or time of day."}
    if point is not None:
        order = sorted(grouped, key=lambda k: (meta[k[1]]["km"], grouped[k][0], k))
    else:
        order = sorted(grouped, key=lambda k: (grouped[k][0], k))
    today = ctx.now().date()
    options = []
    for p, l in order:
        opt = {
            "provider_id": p, "provider": f"{provs[p]['name']}, {provs[p]['title']}", "specialty": provs[p]["specialty"],
            "location_id": l, "location": meta[l]["name"],
            "slots": [{"start": s.isoformat(timespec="minutes"), "say": spoken(s, today)} for s in grouped[(p, l)]],
        }
        if meta[l]["km"] is not None:
            opt["distance_km"] = round(meta[l]["km"], 1)
        options.append(opt)
    out = _bounded(options, limit=MAX_SLOT_OPTIONS, status="ok", today=_today(ctx),
                   appointment_type=t["name"], duration_min=t["duration_min"])
    offered = ctx.session.setdefault("offered", set())
    for o in out["options"]:
        for s in o["slots"]:
            offered.add((o["provider_id"], o["location_id"], type_id, s["start"]))
    sites = {o["location_id"] for o in options}
    if provider_id and not location_id and len(sites) > 1:
        out["instruction"] = "This provider works at several of these locations. Confirm which location before booking."
    if not patient:
        out["warning"] = "Patient not looked up yet: referral and new-patient rules were not applied. Look them up before booking."
    if point is not None and options and options[0].get("distance_km", 0) > FAR_KM:
        out["note"] = f"Nothing near {near}; these are the closest."
    return out


async def list_slots(args: dict, ctx: ToolContext, conn) -> dict:
    """Raw calendar for the naive baseline: the next free times for a provider at a site,
    with NO policy filtering. The commit gate in book_appointment still applies."""
    provider_id, location_id, type_id = args.get("provider_id"), args.get("location_id"), args.get("appointment_type_id")
    t = await conn.fetchrow("SELECT duration_min FROM appointment_types WHERE catalog_version = $1 AND id = $2", ctx.cv, type_id)
    if not t:
        return {"error": f"Unknown appointment type '{type_id}'."}
    slots = await free_slots(conn, ctx.cv, [(provider_id, location_id)], t["duration_min"], ctx.now() + LEAD, ctx.scopes)
    today = ctx.now().date()
    options = [{"start": s.isoformat(timespec="minutes"), "say": spoken(s, today)} for _, _, s in slots]
    offered = ctx.session.setdefault("offered", set())
    for o in options:
        offered.add((provider_id, location_id, type_id, o["start"]))
    return _bounded(options, limit=MAX_SLOT_OPTIONS, today=_today(ctx))


# ---- booking -------------------------------------------------------------------------


def _idempotency_key(cv: str, scope: str, patient: dict, type_id: str, provider_id: str, location_id: str, start: str) -> str:
    who = patient.get("id") or f"{normalize(patient['name'])}|{patient['dob']}"
    raw = "|".join([cv, scope, who, type_id, provider_id, location_id, start])
    return hashlib.sha256(raw.encode()).hexdigest()


async def book_appointment(args: dict, ctx: ToolContext, conn) -> dict:
    type_id, provider_id = args.get("appointment_type_id"), args.get("provider_id")
    location_id, start_text = args.get("location_id"), args.get("start") or args.get("slot")
    patient = ctx.session.get("patient")
    if not patient:
        return {"booked": False, "problems": ["Look the patient up first (full name and date of birth)."]}
    try:
        start = datetime.fromisoformat(start_text)
    except (TypeError, ValueError):
        return {"booked": False, "problems": ["start must be the slot's 'start' value exactly as check_availability returned it."]}
    start_key = start.isoformat(timespec="minutes")
    key = _idempotency_key(ctx.cv, ctx.scope, patient, type_id, provider_id, location_id, start_key)

    existing = await conn.fetchrow(
        "SELECT confirmation FROM appointments WHERE catalog_version = $1 AND scope = $2 AND idempotency_key = $3",
        ctx.cv, ctx.scope, key)
    if existing:
        return {"booked": True, "already_booked": True, "confirmation": existing["confirmation"]}

    t = await conn.fetchrow("SELECT * FROM appointment_types WHERE catalog_version = $1 AND id = $2", ctx.cv, type_id)
    if not t:
        return {"booked": False, "problems": [f"Unknown appointment type '{type_id}'."]}
    # every policy in the set, 1-6, on the exact booking
    offered = ctx.session.get("offered", set())
    conv = Conversation({})
    for p, l, _, _ in offered:
        conv.offered_locations.setdefault(p, set()).add(l)
    broken = ctx.policies.violations(Candidate(provider_id, location_id, type_id), _state(patient), conv)
    if broken:
        return {"booked": False, "problems": [v.reason for v in broken], "policies": [v.policy for v in broken]}
    # no invented times: the exact provider, site, type and time must have been offered
    if (provider_id, location_id, type_id, start_key) not in offered:
        same_slot = [o for o in offered if o[0] == provider_id and o[1] == location_id and o[3] == start_key]
        if len(same_slot) == 1:
            return {"booked": False, "problems": [
                f"That time was offered for appointment_type_id {same_slot[0][2]}, not {type_id}. "
                f"Book it with appointment_type_id {same_slot[0][2]} if that's the visit the caller agreed to."]}
        return {"booked": False, "problems": [
            "That provider, location and time weren't offered in this call. Run check_availability and offer a time first."]}

    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1 || $2))", ctx.cv, provider_id)
        free = await free_slots(conn, ctx.cv, [(provider_id, location_id)], t["duration_min"], start, ctx.scopes,
                                per_pair=1, horizon_days=0)
        if not free or free[0][2] != start or start < ctx.now() + LEAD:
            return {"booked": False, "problems": ["That time was just taken or is no longer available. Offer another."]}
        patient_id = patient.get("id")
        if not patient_id:
            patient_id = "new_" + key[:10]
            await conn.execute(
                "INSERT INTO patients VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                ctx.cv, ctx.scope, patient_id, patient["name"], normalize(patient["name"]), date.fromisoformat(patient["dob"]))
            patient["id"] = patient_id
        confirmation = "PR-" + key[:6].upper()
        await conn.execute(
            """INSERT INTO appointments (catalog_version, scope, confirmation, idempotency_key, patient_id,
                   provider_id, location_id, appointment_type_id, starts_at, ends_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            ctx.cv, ctx.scope, confirmation, key, patient_id, provider_id, location_id, type_id,
            start, start + timedelta(minutes=t["duration_min"]))
    names = await conn.fetchrow(
        """SELECT p.name AS provider, l.name AS location FROM providers p, locations l
           WHERE p.catalog_version = $1 AND l.catalog_version = $1 AND p.id = $2 AND l.id = $3""",
        ctx.cv, provider_id, location_id)
    ctx.session.setdefault("booked", []).append(confirmation)
    return {
        "booked": True, "confirmation": confirmation,
        "summary": f"{t['name']} with {names['provider']} at {names['location']}, {spoken(start, ctx.now().date())}",
    }


# ---- registry --------------------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    required: list
    run: Callable[[dict, ToolContext, object], Awaitable[dict]]
    enums: dict = field(default_factory=dict)   # property -> ctx.enums key, filled per catalog
    hidden: bool = False                        # not offered in the builder UI (eval baseline only)


_ID = {"type": "string"}

TOOLS = {
    t.name: t
    for t in [
        Tool(
            "lookup_patient",
            "Look the caller up by full name and date of birth. Tells you whether they are an existing or new patient "
            "and which referrals are on file. Do this before checking availability or booking.",
            {"full_name": {"type": "string"},
             "date_of_birth": {"type": "string", "description": "YYYY-MM-DD"}},
            ["full_name", "date_of_birth"],
            lookup_patient,
        ),
        Tool(
            "search_appointment_types",
            "Match what the caller wants to the clinic's appointment types, using the caller's own words. Returns the "
            "best candidates with the facts that tell them apart; when two are easily confused it returns status 'ask' "
            "with the question to put to the caller.",
            {"query": {"type": "string", "description": "The caller's own words, e.g. 'skin check' or 'my knee hurts'."},
             "specialty": {"type": "string", "description": "Optional specialty filter."},
             "provider_id": {**_ID, "description": "Only types this provider can do."},
             "location_id": {**_ID, "description": "Only types bookable at this location."}},
            ["query"],
            search_appointment_types,
            enums={"specialty": "specialty"},
        ),
        Tool(
            "find_providers",
            "Find providers by name, specialty, appointment type, location or language. Names are not unique: when "
            "several match it returns status 'ask' and what tells them apart. Includes counts by specialty.",
            {"name": {"type": "string", "description": "As the caller said it, e.g. 'Dr. Nguyen'."},
             "specialty": {"type": "string"},
             "appointment_type_id": {**_ID, "description": "Only providers who can do this type."},
             "location_id": _ID,
             "language": {"type": "string"},
             "accepting_new_patients": {"type": "boolean"}},
            [],
            find_providers,
            enums={"specialty": "specialty", "language": "language"},
        ),
        Tool(
            "find_locations",
            "Clinic locations with address, phone, opening hours and services. Filter by name, a capability, a day "
            "they're open, what they can do, or rank by distance from a place the caller names "
            "(a neighbourhood, street or landmark).",
            {"name": {"type": "string"},
             "near": {"type": "string", "description": "Place the caller named, e.g. 'Sunset' or 'Geary Blvd'."},
             "capability": {"type": "string"},
             "open_on": {"type": "string", "enum": DAY_NAMES},
             "appointment_type_id": {**_ID, "description": "Only locations where this type is bookable."},
             "provider_id": {**_ID, "description": "Only locations where this provider works."}},
            [],
            find_locations,
            enums={"capability": "capability"},
        ),
        Tool(
            "check_availability",
            "Open slots for an appointment type: soonest first, or nearest first when 'near' is given. Optionally "
            "narrowed to a provider and/or location. Applies the patient's referral and new-patient rules. Only "
            "returns provider-location pairs that can actually host the type. Offer only times it returns.",
            {"appointment_type_id": _ID,
             "provider_id": _ID,
             "location_id": _ID,
             "near": {"type": "string", "description": "A place the caller named, to rank by distance."},
             "on_or_after": {"type": "string", "description": "YYYY-MM-DD, when the caller can't come sooner."},
             "part_of_day": {"type": "string", "enum": ["any", "morning", "afternoon"]}},
            ["appointment_type_id"],
            check_availability,
        ),
        Tool(
            "list_slots",
            "Raw calendar (naive baseline only): next free times for a provider at a location, for a type's duration. "
            "Does NOT check any clinic rule.",
            {"provider_id": _ID, "location_id": _ID, "appointment_type_id": _ID},
            ["provider_id", "location_id", "appointment_type_id"],
            list_slots,
            hidden=True,
        ),
        Tool(
            "book_appointment",
            "Book a slot the caller explicitly confirmed. Pass the slot's 'start' exactly as check_availability "
            "returned it. Every clinic rule is re-checked; a rejection comes back with plain reasons.",
            {"appointment_type_id": _ID, "provider_id": _ID, "location_id": _ID,
             "start": {"type": "string", "description": "The slot's 'start' value, e.g. 2026-10-13T09:30."}},
            ["appointment_type_id", "provider_id", "location_id", "start"],
            book_appointment,
        ),
    ]
}


def schema_properties(tool: Tool, ctx: Optional[ToolContext]) -> dict:
    """The tool's JSON-schema properties, with this catalog's finite vocabularies as enums."""
    props = {k: dict(v) for k, v in tool.properties.items()}
    if ctx is not None:
        for prop, key in tool.enums.items():
            if ctx.enums.get(key):
                props[prop]["enum"] = ctx.enums[key]
    return props


_ENUM_ARGS = {"specialty": "specialty", "capability": "capability", "language": "language"}


async def _validate(args: dict, ctx: ToolContext, conn) -> tuple:
    """Check ids and enum values before a tool runs. An unknown value never becomes a silent
    empty result (the model would read that as "we have none"). A name passed where an id
    belongs is resolved when it is unambiguous; otherwise the error says how to fix the call.
    Returns (args, error dict or None, notes)."""
    args = {k: v for k, v in args.items() if v not in (None, "")}
    notes = []
    for key, enum in _ENUM_ARGS.items():
        if key in args:
            allowed = ctx.enums.get(enum, [])
            match = next((a for a in allowed if a.lower() == str(args[key]).lower()), None)
            if match is None:
                hint = " For a kind of visit, use search_appointment_types instead." if key == "capability" else ""
                return args, {"error": f"{key} '{args[key]}' is not one of: {', '.join(allowed)}.{hint}"}, notes
            args[key] = match
    if "appointment_type_id" in args:
        tid = args["appointment_type_id"]
        if not await conn.fetchval("SELECT 1 FROM appointment_types WHERE catalog_version = $1 AND id = $2", ctx.cv, tid):
            rows = await conn.fetch("SELECT id FROM appointment_types WHERE catalog_version = $1 AND lower(name) = lower($2)", ctx.cv, str(tid))
            if len(rows) != 1:
                return args, {"error": f"appointment_type_id '{tid}' is not an id. Use an appointment_type_id from search_appointment_types."}, notes
            args["appointment_type_id"] = rows[0]["id"]
            notes.append(f"read appointment type '{tid}' as {rows[0]['id']}")
    if "provider_id" in args:
        pid = args["provider_id"]
        if not await conn.fetchval("SELECT 1 FROM providers WHERE catalog_version = $1 AND id = $2", ctx.cv, pid):
            q = _HONORIFIC.sub("", normalize(str(pid)))
            rows = await conn.fetch(
                "SELECT id, name FROM providers WHERE catalog_version = $1 AND name_score($2, search_name) >= 0.8", ctx.cv, q)
            if len(rows) != 1:
                err = {"error": f"provider_id '{pid}' is not an id"
                       + (f" and that name fits {len(rows)} providers" if rows else "")
                       + ". Look the provider up with find_providers (and ask which one if several match)."}
                if len(rows) > 1:
                    err.update({"status": "ask", "ambiguous_arg": "provider_id",
                                "candidates": [r["name"] for r in rows][:MAX_RESULTS]})
                return args, err, notes
            args["provider_id"] = rows[0]["id"]
            notes.append(f"read provider '{pid}' as {rows[0]['name']}")
    if "location_id" in args:
        lid = args["location_id"]
        if not await conn.fetchval("SELECT 1 FROM locations WHERE catalog_version = $1 AND id = $2", ctx.cv, lid):
            q = normalize(str(lid))
            rows = await conn.fetch(
                """SELECT id, name FROM locations WHERE catalog_version = $1
                     AND (position($2 IN lower(name)) > 0 OR lower(coalesce(place, '')) = $2)""", ctx.cv, q)
            if len(rows) != 1:
                err = {"error": f"location_id '{lid}' is not an id. Use a location_id from find_locations."}
                if len(rows) > 1:
                    err.update({"status": "ask", "ambiguous_arg": "location_id", "candidates": [r["name"] for r in rows]})
                return args, err, notes
            args["location_id"] = rows[0]["id"]
            notes.append(f"read location '{lid}' as {rows[0]['name']}")
    return args, None, notes


async def run_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    await ctx.prepare()
    pool = await db.pool()
    async with pool.acquire() as conn:
        args, error, notes = await _validate(args, ctx, conn)
        if error:
            return error
        result = await TOOLS[name].run(args, ctx, conn)
        if notes and isinstance(result, dict):
            result["resolved"] = notes
        return result


def describe_tools() -> list:
    """Tool catalog for the builder UI."""
    return [{"name": t.name, "description": t.description, "parameters": list(t.properties)}
            for t in TOOLS.values() if not t.hidden]


def result_size(result: dict) -> Optional[int]:
    if "shown" in result:
        return result["shown"]
    return None
