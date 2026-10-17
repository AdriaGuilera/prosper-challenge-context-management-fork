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
from datetime import date, datetime, time
from typing import Optional

from store import calendar
from store.bundle import Bundle, normalize
from store.hours import DAY_NAMES, HoursError, parse_hours
from store.policies import PatientState, triple_problems, valid_triples

TIERS = {"ideal": 1.0, "acceptable": 0.7, "suboptimal": 0.3, "wrong": 0.0}
TIE_KM = 0.05


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
    ask_labels: dict = field(default_factory=dict)  # must_ask -> words a question that asks it would name
    patient_ref: Optional[object] = None            # records a booking may land on: {ids} of this person, or "new"
    target: Optional[dict] = None                   # the one booking this caller is aiming for, in this catalog
    notes: list = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "outcome": self.outcome, "ideal_types": [t for t, v in self.type_tier.items() if v == "ideal"],
            "constrained": len(self.constrained), "best_locations": sorted(self.best_locations),
            "soonest": self.soonest.isoformat() if self.soonest else None,
            "must_ask": self.must_ask, "must_state": self.must_state, "info": self.info, "notes": self.notes,
            "target": target_summary(self.target),
        }


def target_summary(t: Optional[dict]) -> Optional[dict]:
    if not t:
        return None
    return {"booking": t["text"], "start": t["start"].isoformat(timespec="minutes"), "time_rule": t["time_rule"],
            "equally_good": t["equally_good"], "ids": [t["appointment_type_id"], t["provider_id"], t["location_id"]]}


def when(dt: datetime) -> str:
    """'Wednesday October 14 at 11am' / '... at 2:45pm'."""
    clock = dt.strftime("%-I%p") if dt.minute == 0 else dt.strftime("%-I:%M%p")
    return f"{dt:%A %B %-d} at {clock.lower()}"


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
    return {loc_id: tuple(m["at"]) for loc_id, m in bundle.places.items()}


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


def seeded_patient_id(name: str) -> str:
    """The registry id the eval seeds for an existing persona (evals/run.py)."""
    return "pat_" + normalize(name).replace(" ", "_")


def patient_state(bundle: Bundle, record: dict, clock: datetime) -> PatientState:
    ids_by_name = bundle.type_ids_by_name()
    refs = set()
    # a new patient has no record, so no referral of theirs can be on file (the runner seeds none)
    for r in record.get("referrals", []) if record.get("existing") else []:
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
    rec = persona["caller"].get("record", {})
    if rec.get("existing"):
        # the eval seeds its own copy; the catalog's registry may already hold the same person
        same = {p["id"] for p in bundle.patients
                if normalize(p["name"]) == normalize(persona["caller"]["name"]) and p["dob"] == persona["caller"]["dob"]}
        o.patient_ref = {seeded_patient_id(persona["caller"]["name"])} | same
    else:
        o.patient_ref = "new"
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
            offering = {tr[0] for tr in o.candidates if tr[0] in by_name}
            if len(offering) > 1:
                o.must_ask.append("provider")
                provs = bundle.providers
                o.ask_labels["provider_says"] = normalize(pspec.get("says") or pspec["name"]).split()[-1]
                o.ask_labels["provider"] = sorted({normalize(provs[p]["name"].replace("Dr.", "")).split()[0] for p in offering}
                                                  | {provs[p]["specialty"].lower() for p in offering})
    # aim at the best tier that can actually be booked: if no ideal/acceptable type is bookable
    # here but a suboptimal one is, that one becomes the acceptable answer for this catalog
    good = {tr for tr in constrained if o.type_tier.get(tr[2]) in ("ideal", "acceptable")}
    if constrained and not good:
        for tr in constrained:
            o.type_tier[tr[2]] = "acceptable"
        o.notes.append("only a suboptimal type is bookable in this catalog; promoted to acceptable")
        good = set(constrained)
    o.constrained = constrained
    target = good or constrained   # place and time are judged against the best bookable tier

    if o.outcome == "booking" and not constrained:
        o.outcome = "no_booking"
        o.notes.append("persona wants a booking, but nothing in this catalog satisfies it")
    if o.outcome == "no_booking":
        o.must_state += _why_not(bundle, o, pspec, structural)

    # place preference
    if exp.get("near") and constrained:
        dist = distances(bundle, exp["near"])
        if dist:
            best = min(dist[tr[1]] for tr in target)
            o.best_locations = {tr[1] for tr in target if dist[tr[1]] - best <= TIE_KM}
            if min(dist.values()) > bundle.gazetteer.get("far_km", 9.0):
                o.must_state.append("far")   # nothing at all near the caller's place
            if len(o.best_locations) > 1:
                o.notes.append("proximity tie: " + ", ".join(sorted(o.best_locations)))

    # time preference
    o.part_of_day = exp.get("part_of_day")
    if exp.get("on_or_after"):
        o.on_or_after = date.fromisoformat(exp["on_or_after"])
    if constrained:
        options = _first_slots(bundle, anchor, clock, target, o)
        soonest_matters = exp.get("time") == "soonest"
        if soonest_matters and options:
            o.soonest = min(s for _, s, *_ in options)
        if o.outcome == "booking":
            o.target = _target(bundle, options, o, soonest_matters)

    # "visit:A|B": the caller's words fit several visits, so the agent must ask which one;
    # only where at least two of them are offered here. A persona lists it only when a
    # wrong pick is a different procedure or body part (MRI brain vs spine); when either
    # visit meets the need, it gives both tiers instead and asking is optional.
    # Every ask narrows a booking, so none is required when no booking is possible.
    for ask in exp.get("must_ask", []) if o.outcome == "booking" else []:
        if ask.startswith("visit:"):
            names = ask.split(":", 1)[1].split("|")
            members = {i for n in names for i in ids_by_name.get(normalize(n), [])}
            offered = {tr[2] for tr in structural}
            if len(members & offered) >= 2:
                o.must_ask.append(ask)
                o.ask_labels[ask] = [n.lower() for n in names]
        elif ask == "place":
            o.must_ask.append(ask)
            o.ask_labels[ask] = sorted({p["name"].lower() for p in bundle.gazetteer["places"] if p["kind"] != "street"}
                                       | {l["name"].lower() for l in cat["locations"]})
    o.must_ask = list(dict.fromkeys(o.must_ask))
    o.must_state = list(dict.fromkeys(o.must_state))
    return o


def _why_not(bundle: Bundle, o: Oracle, pspec: Optional[dict], structural: set) -> list:
    """Every true reason the request can't be booked, as ONE any-of entry ("a|b"): stating any
    true reason is correct; the agent is not graded on picking our favourite."""
    reasons = []
    wanted = set(o.type_tier)
    offered = {tr for tr in structural if tr[2] in wanted}
    if pspec and o.provider_strict and pspec.get("name") and not o.provider_match:
        reasons.append("no_provider")
    if not wanted or not offered:
        reasons.append("not_offered")
    if pspec and o.provider_strict and o.provider_match and not {tr for tr in offered if tr[0] in o.provider_match}:
        reasons += ["provider_cannot", "no_provider"]
    pool = {tr for tr in offered if not pspec or not o.provider_strict or tr[0] in o.provider_match}
    problems = [set(triple_problems(bundle.catalog, *tr, patient=o.patient)) for tr in pool]
    if any("referral_required" in p for p in problems):
        reasons.append("referral")
    new_blocked = [p for p in problems if p & {"type_closed_to_new_patients", "provider_not_accepting_new_patients"}]
    if new_blocked:
        reasons += ["not_accepting_new", "new_patient"]
    reasons = list(dict.fromkeys(reasons)) or ["cannot_book"]
    return ["|".join(reasons)]


_CALENDARS: dict = {}


def _calendar(bundle: Bundle, anchor: date) -> tuple:
    key = (bundle.name, bundle.seed, anchor)
    if key not in _CALENDARS:
        schedules = calendar.generate_schedules(bundle.catalog, bundle.seed, bundle.calendar.get("density", "normal"))
        bookings = calendar.generate_prebookings(bundle.catalog, schedules, bundle.seed, anchor,
                                                 bundle.calendar.get("prebook_rate", 0.3))
        _CALENDARS[key] = (schedules, bookings)
    return _CALENDARS[key]


def _first_slots(bundle: Bundle, anchor: date, clock: datetime, triples: set, o: Oracle) -> list:
    """(tier rank, first allowed slot, provider, location, type) for every triple at a suitable
    location: its earliest free slot after the lead time, the caller's earliest date and part of day."""
    schedules, bookings = _calendar(bundle, anchor)
    start = clock + calendar.LEAD
    if o.on_or_after:
        start = max(start, datetime.combine(o.on_or_after, time()))
    rank = {"ideal": 0, "acceptable": 1, "suboptimal": 2}
    options = []
    for prov, loc, tid in triples:
        if o.best_locations and loc not in o.best_locations:
            continue
        for s in calendar.free_slots(schedules, bookings, prov, loc, bundle.types[tid]["duration_min"], start):
            if (o.part_of_day == "morning" and s.hour >= 12) or (o.part_of_day == "afternoon" and s.hour < 12):
                continue
            options.append((rank.get(o.type_tier.get(tid), 3), s, prov, loc, tid))
            break
    return options


def _target(bundle: Bundle, options: list, o: Oracle, soonest_matters: bool) -> Optional[dict]:
    """The single booking this caller is aiming for: the best visit tier, then the nearest suitable
    location, then the earliest slot their time preferences allow, then ids (a stable tie-break).
    `equally_good` counts the other bookings the grading would score the same."""
    if not options:
        return None
    best = min(options)
    same = [x for x in options if x[0] == best[0] and (not soonest_matters or x[1] == best[1])]
    rule = "the soonest slot" if soonest_matters else "any time that suits the caller"
    if o.part_of_day:
        rule += f", in the {o.part_of_day}"
    if o.on_or_after:
        rule += f", not before {o.on_or_after:%A %B %-d}"
    _, s, prov, loc, tid = best
    text = (f"{bundle.types[tid]['name']} with {bundle.providers[prov]['name']} at "
            f"{bundle.locations[loc]['name']}, {when(s)}")
    return {"appointment_type_id": tid, "provider_id": prov, "location_id": loc, "start": s, "text": text,
            "time_rule": rule, "equally_good": len(same) - 1}


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
        loc = next((l for l in cat["locations"] if l["name"] == spec["location"]), None)
        if loc is None:  # a location named in one catalog and absent here: the answer is that there's none
            return {"kind": "location_open_on", "location": spec["location"], "day": spec["day"], "exists": False}
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
        if not dist:
            return {"kind": "nearest_to", "place": item["nearest_to"], "locations": []}
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
