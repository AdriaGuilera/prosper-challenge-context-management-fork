#
# Oracle: a persona's ground truth for ONE catalog, computed, never stored.
#
# The same persona ("Dr. Chen, echocardiogram, near Downtown") has different right
# answers in different catalogs: other Chens, other imaging sites, no echo at all,
# a referral rule that differs. So the expectation in the persona file is a spec in
# names, and this module evaluates it against the catalog with the Python reference
# engine (store/policies.py), the seeded calendar (store/calendar.py) and the
# gazetteer, without touching the database or the tools under test.
#

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from store import calendar
from store.bundle import Bundle, normalize
from store.hours import DAY_NAMES, HoursError, parse_hours
from store.policies import PatientState, triple_problems, valid_triples

TIERS = {"ideal": 1.0, "acceptable": 0.7, "suboptimal": 0.3, "wrong": 0.0}
TIE_KM = 0.05


def tier_min(*tiers: str) -> str:
    return min(tiers, key=lambda t: TIERS[t])


@dataclass
class Oracle:
    persona_id: str
    catalog: str
    outcome: str                                   # booking | no_booking | information
    patient: PatientState
    type_tier: dict = field(default_factory=dict)  # type_id -> tier name
    candidates: set = field(default_factory=set)   # valid triples for this patient and the wanted types
    constrained: set = field(default_factory=set)  # ... after hard preferences
    provider_match: set = field(default_factory=set)
    provider_strict: bool = True
    best_locations: set = field(default_factory=set)
    soonest: Optional[datetime] = None
    part_of_day: Optional[str] = None
    on_or_after: Optional[date] = None
    must_ask: list = field(default_factory=list)
    must_state: list = field(default_factory=list)
    info: list = field(default_factory=list)       # resolved information expectations
    notes: list = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "outcome": self.outcome, "ideal_types": [t for t, v in self.type_tier.items() if v == "ideal"],
            "constrained": len(self.constrained), "best_locations": sorted(self.best_locations),
            "soonest": self.soonest.isoformat() if self.soonest else None,
            "must_ask": self.must_ask, "must_state": self.must_state, "info": self.info, "notes": self.notes,
        }


# ---- geography (independent of PostGIS) ------------------------------------------


def _gz_index(bundle: Bundle) -> dict:
    idx = {}
    for p in bundle.gazetteer["places"]:
        for n in [p["name"], *p["aliases"]]:
            idx[normalize(n)] = p
    return idx


def place_point(bundle: Bundle, name: str) -> Optional[tuple]:
    p = _gz_index(bundle).get(normalize(name))
    return tuple(p["points"][0]) if p and len(p["points"]) == 1 else None


def location_points(bundle: Bundle) -> dict:
    idx = _gz_index(bundle)
    out = {}
    for loc_id, m in bundle.places.items():
        if m.get("at"):
            out[loc_id] = tuple(m["at"])
        elif normalize(m["place"]) in idx:
            out[loc_id] = tuple(idx[normalize(m["place"])]["points"][0])
    return out


def distances(bundle: Bundle, place: str) -> dict:
    pt = place_point(bundle, place)
    if pt is None:
        return {}
    return {loc: math.dist(pt, xy) for loc, xy in location_points(bundle).items()}


# ---- providers -----------------------------------------------------------------------


def provider_matches(bundle: Bundle, spec: dict) -> set:
    name_tokens = [t for t in normalize(spec.get("name", "")).split() if t not in ("dr", "doctor")]
    out = set()
    for p in bundle.catalog["providers"]:
        tokens = normalize(p["name"]).split()
        if name_tokens and not all(t in tokens for t in name_tokens):
            continue
        if spec.get("specialty") and p["specialty"] != spec["specialty"]:
            continue
        if spec.get("language") and spec["language"] not in p.get("languages", []):
            continue
        out.add(p["id"])
    return out


# ---- the oracle ------------------------------------------------------------------------


def patient_state(bundle: Bundle, record: dict, clock: datetime) -> PatientState:
    ids_by_name = bundle.type_ids_by_name()
    refs = set()
    for r in record.get("referrals", []):
        name = r if isinstance(r, str) else r["type"]
        expires = None if isinstance(r, str) else r.get("expires")
        if expires and date.fromisoformat(expires) < clock.date():
            continue  # an expired referral is not on file
        refs |= set(ids_by_name.get(normalize(name), []))
    return PatientState(is_new=not record.get("existing", False), referral_type_ids=frozenset(refs))


def compute_oracle(persona: dict, bundle: Bundle, anchor: date, clock: datetime) -> Oracle:
    exp = persona["expect"]
    cat = bundle.catalog
    patient = patient_state(bundle, persona["caller"].get("record", {}), clock)
    o = Oracle(persona["id"], bundle.name, exp["outcome"], patient)
    ids_by_name = bundle.type_ids_by_name()
    for tier in ("ideal", "acceptable", "suboptimal"):
        for name in exp.get("types", {}).get(tier, []):
            for tid in ids_by_name.get(normalize(name), []):
                o.type_tier.setdefault(tid, tier)

    if o.outcome == "information":
        o.info = [_resolve_info(bundle, item) for item in exp.get("info", [])]
        return o

    structural = valid_triples(cat)
    o.candidates = {tr for tr in valid_triples(cat, patient) if tr[2] in o.type_tier}
    wanted_ideal = {t for t, v in o.type_tier.items() if v in ("ideal", "acceptable")}

    # provider preference
    pspec = exp.get("provider")
    constrained = set(o.candidates)
    if pspec:
        o.provider_strict = pspec.get("strict", True)
        o.provider_match = provider_matches(bundle, pspec)
        preferred = {tr for tr in constrained if tr[0] in o.provider_match}
        if o.provider_strict:
            constrained = preferred
        elif preferred:
            constrained = preferred
        elif o.provider_match:
            # the named provider exists but can't take this patient: say why, offer others
            blocked = {tr for tr in structural if tr[0] in o.provider_match and tr[2] in o.type_tier}
            if blocked and patient.is_new and any(
                    "provider_not_accepting_new_patients" in triple_problems(cat, *tr, patient=patient) for tr in blocked):
                o.must_state.append("not_accepting_new")
        if pspec.get("says") or pspec.get("name"):
            # what the caller first SAYS ("Dr. Chen"): if it fits several providers who could do this, ask
            by_name = provider_matches(bundle, {"name": pspec.get("says") or pspec["name"]})
            if len({tr[0] for tr in o.candidates if tr[0] in by_name}) > 1:
                o.must_ask.append("provider")
    o.constrained = constrained

    if o.outcome == "booking" and not constrained:
        o.outcome = "no_booking"
        o.notes.append("persona wants a booking, but nothing in this catalog satisfies it")
    if o.outcome == "no_booking":
        o.must_state += _why_not(bundle, o, pspec, structural)

    # place preference
    if exp.get("near") and constrained:
        dist = distances(bundle, exp["near"])
        if dist:
            best = min(dist[tr[1]] for tr in constrained)
            o.best_locations = {tr[1] for tr in constrained if dist[tr[1]] - best <= TIE_KM}
            if best > bundle.gazetteer.get("far_km", 9.0):
                o.must_state.append("far")
            if len(o.best_locations) > 1:
                o.notes.append("proximity tie: " + ", ".join(sorted(o.best_locations)))

    # time preference
    o.part_of_day = exp.get("part_of_day")
    if exp.get("on_or_after"):
        o.on_or_after = date.fromisoformat(exp["on_or_after"])
    if exp.get("time") == "soonest" and constrained:
        o.soonest = _soonest(bundle, anchor, clock, constrained, o)

    # registered-cluster asks only apply where the cluster exists among offered types
    for ask in exp.get("must_ask", []):
        if ask.startswith("cluster:"):
            cid = ask.split(":", 1)[1]
            c = next((c for c in bundle.clusters if c["id"] == cid), None)
            if c:
                members = {i for m in c["members"] for i in ids_by_name.get(normalize(m), [])}
                offered = {tr[2] for tr in structural}
                if len(members & offered) >= 2 and o.outcome == "booking":
                    o.must_ask.append(ask)
        elif ask == "place":
            o.must_ask.append(ask)
    o.must_ask = list(dict.fromkeys(o.must_ask))
    o.must_state = list(dict.fromkeys(o.must_state))
    return o


def _why_not(bundle: Bundle, o: Oracle, pspec: Optional[dict], structural: set) -> list:
    wanted = set(o.type_tier)
    offered = {tr for tr in structural if tr[2] in wanted}
    if not wanted or not offered:
        return ["not_offered"]
    if pspec and o.provider_strict:
        if not o.provider_match:
            return ["no_provider"]
        if not {tr for tr in offered if tr[0] in o.provider_match}:
            return ["provider_cannot"]
    reasons = []
    pool = {tr for tr in offered if not pspec or not o.provider_strict or tr[0] in o.provider_match}
    problems = [set(triple_problems(bundle.catalog, *tr, patient=o.patient)) for tr in pool]
    if any("referral_required" in p for p in problems):
        reasons.append("referral")
    new_blocked = [p for p in problems if p & {"type_closed_to_new_patients", "provider_not_accepting_new_patients"}]
    if new_blocked:
        only_provider = all("type_closed_to_new_patients" not in p for p in new_blocked)
        reasons.append("not_accepting_new" if only_provider else "new_patient")
    return reasons or ["cannot_book"]


def _soonest(bundle: Bundle, anchor: date, clock: datetime, triples: set, o: Oracle) -> Optional[datetime]:
    schedules = calendar.generate_schedules(bundle.catalog, bundle.seed, bundle.calendar.get("density", "normal"))
    bookings = calendar.generate_prebookings(bundle.catalog, schedules, bundle.seed, anchor,
                                             bundle.calendar.get("prebook_rate", 0.3))
    types = bundle.types
    start = clock
    if o.on_or_after:
        start = max(start, datetime.combine(o.on_or_after, time()))
    best = None
    for prov, loc, tid in triples:
        if o.best_locations and loc not in o.best_locations:
            continue
        for s in calendar.free_slots(schedules, bookings, prov, loc, types[tid]["duration_min"], start):
            if o.part_of_day == "morning" and s.hour >= 12:
                continue
            if o.part_of_day == "afternoon" and s.hour < 12:
                continue
            if best is None or s < best:
                best = s
            break
    return best


def _resolve_info(bundle: Bundle, item: dict) -> dict:
    cat = bundle.catalog
    if "open_on" in item:
        day = DAY_NAMES.index(item["open_on"])
        open_locs = []
        for l in cat["locations"]:
            try:
                if day in parse_hours(l["hours"]):
                    open_locs.append(l["name"])
            except HoursError:
                pass
        return {"kind": "open_on", "day": item["open_on"], "open_locations": open_locs}
    if "location_open_on" in item:
        spec = item["location_open_on"]
        loc = next(l for l in cat["locations"] if l["name"] == spec["location"])
        day = DAY_NAMES.index(spec["day"])
        try:
            intervals = parse_hours(loc["hours"]).get(day, [])
        except HoursError:
            intervals = []
        fmt = lambda t: (f"{t.hour % 12 or 12}{'am' if t.hour < 12 else 'pm'}" if t.minute == 0  # noqa: E731
                         else f"{t.hour % 12 or 12}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}")
        return {"kind": "location_open_on", "location": spec["location"], "day": spec["day"],
                "open": bool(intervals), "opens": [fmt(a) for a, _ in intervals]}
    if "nearest_to" in item:
        dist = distances(bundle, item["nearest_to"])
        best = min(dist.values())
        names = {l["id"]: l["name"] for l in cat["locations"]}
        return {"kind": "nearest_to", "place": item["nearest_to"],
                "locations": sorted(names[k] for k, d in dist.items() if d - best <= TIE_KM)}
    if "count_providers" in item:
        spec = item["count_providers"]
        return {"kind": "count", "spec": spec, "count": len(provider_matches(bundle, spec))}
    if "any_provider" in item:
        spec = item["any_provider"]
        ids = provider_matches(bundle, spec)
        names = [p["name"] for p in cat["providers"] if p["id"] in ids]
        return {"kind": "any_provider", "spec": spec, "providers": names}
    raise ValueError(f"unknown info expectation {item}")


# ---- grading a booking ----------------------------------------------------------------


def grade_booking(o: Oracle, booking: Optional[dict], bundle: Bundle) -> tuple:
    """(tier, reasons) for the final booking state. booking: {provider_id, location_id,
    appointment_type_id, starts_at (datetime)} or None."""
    if o.outcome == "information":
        return ("ideal", []) if booking is None else ("wrong", ["booked during an information-only call"])
    if o.outcome == "no_booking":
        if booking is None:
            return "ideal", []
        if booking["appointment_type_id"] not in o.type_tier and not triple_problems(
                bundle.catalog, booking["provider_id"], booking["location_id"], booking["appointment_type_id"], patient=o.patient):
            # the wanted visit was correctly refused; the caller then took a different, valid one
            return "acceptable", ["booked a different visit the caller agreed to"]
        return "wrong", ["booked something the oracle says can't be booked"]
    if booking is None:
        return "wrong", ["no booking made"]
    tr = (booking["provider_id"], booking["location_id"], booking["appointment_type_id"])
    problems = triple_problems(bundle.catalog, *tr, patient=o.patient)
    if problems:
        return "wrong", [f"invalid booking: {', '.join(problems)}"]
    tier = o.type_tier.get(tr[2])
    if tier is None:
        return "wrong", [f"wrong appointment type {tr[2]}"]
    reasons = [] if tier == "ideal" else [f"type is {tier}"]
    if o.provider_match:
        if tr[0] not in o.provider_match:
            if o.provider_strict:
                return "wrong", ["not the provider the caller asked for"]
            if {c for c in o.constrained if c[0] in o.provider_match}:
                tier = tier_min(tier, "suboptimal")
                reasons.append("preferred provider was available but not booked")
    elif o.constrained and tr not in o.constrained:
        tier = tier_min(tier, "suboptimal")
        reasons.append("outside the caller's stated preferences")
    if o.best_locations and tr[1] not in o.best_locations:
        tier = tier_min(tier, "acceptable" if tier == "ideal" else tier)
        reasons.append("not the nearest suitable location")
    start = booking["starts_at"]
    if o.on_or_after and start.date() < o.on_or_after:
        return "wrong", ["before the date the caller can come"]
    if o.part_of_day == "morning" and start.hour >= 12 or o.part_of_day == "afternoon" and start.hour < 12:
        tier = tier_min(tier, "suboptimal")
        reasons.append(f"not in the {o.part_of_day}")
    if o.soonest:
        if start > o.soonest:
            late = start - o.soonest
            tier = tier_min(tier, "acceptable" if late <= timedelta(days=2) else "suboptimal")
            reasons.append(f"{late} later than the soonest suitable slot")
    return tier, reasons
