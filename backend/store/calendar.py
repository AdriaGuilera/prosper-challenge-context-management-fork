#
# Mocked calendar (decision 002): weekly working blocks per provider, generated
# deterministically from a seed and clipped to the location's opening hours, plus
# a deterministic set of pre-existing bookings so "soonest" is not just 8am Monday.
#
# Both the ingest (which writes the rows) and the eval oracle (which predicts the
# soonest slot) call these generators; slot arithmetic is reimplemented in SQL by
# the tools and in `free_slots` here, so the two are checked against each other.
#

import random
from datetime import date, datetime, time, timedelta

from .hours import parse_hours, HoursError

HORIZON_DAYS = 21
LEAD = timedelta(minutes=30)   # never offer a slot starting sooner than this (applied by callers)

DENSITY = {
    # days per week a provider works, and the share of an open day they cover
    "normal": {"days": (3, 5), "cover": (0.5, 1.0)},
    "sparse": {"days": (1, 2), "cover": (0.25, 0.5)},
}


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


def location_week(catalog: dict) -> dict:
    """loc id -> parsed week; unparseable hours yield no open days."""
    out = {}
    for loc in catalog["locations"]:
        try:
            out[loc["id"]] = parse_hours(loc["hours"])
        except HoursError:
            out[loc["id"]] = {}
    return out


def generate_schedules(catalog: dict, seed: int, density: str = "normal") -> list:
    """[(provider_id, location_id, weekday 1..7, start time, end time)].

    A provider works at most one location per weekday, so no provider is ever in two
    places at once. Blocks are on a 30-minute grid inside the location's hours.
    """
    rng = random.Random(seed)
    week = location_week(catalog)
    knobs = DENSITY.get(density, DENSITY["normal"])
    rows = []
    for p in sorted(catalog["providers"], key=lambda p: p["id"]):
        # (weekday, location) pairs where this provider could work at all
        options = [(d, loc) for loc in p["location_ids"] for d in week.get(loc, {})]
        days = sorted({d for d, _ in options})
        if not days:
            continue
        n_days = min(len(days), rng.randint(*knobs["days"]))
        chosen = sorted(rng.sample(days, n_days))
        locs = list(p["location_ids"])
        rng.shuffle(locs)
        for i, d in enumerate(chosen):
            # rotate through the provider's sites so each one gets time
            candidates = [loc for loc in locs if d in week.get(loc, {})]
            loc = candidates[i % len(candidates)]
            for opens, closes in week[loc][d]:
                span = _minutes(closes) - _minutes(opens)
                length = max(60, int(span * rng.uniform(*knobs["cover"])) // 30 * 30)
                length = min(length, span // 30 * 30)
                if length <= 0:
                    continue
                slack = (span - length) // 30
                start = _minutes(opens) + 30 * rng.randint(0, max(0, slack))
                rows.append((p["id"], loc, d + 1, _time(start), _time(start + length)))
    return rows


def _iter_days(start: date, days: int):
    for i in range(days):
        yield start + timedelta(days=i)


def generate_prebookings(catalog: dict, schedules: list, seed: int, anchor: date,
                         rate: float = 0.3) -> list:
    """[(provider_id, location_id, type_id, start datetime, end datetime)] over the horizon.

    Each pre-booking uses a type the provider offers and sits on that type's own grid
    within a block, so it overlaps cleanly with whatever the tools compute.
    """
    rng = random.Random(seed * 7919 + 1)
    providers = {p["id"]: p for p in catalog["providers"]}
    types = {t["id"]: t for t in catalog["appointment_types"]}
    out = []
    for day in _iter_days(anchor, HORIZON_DAYS):
        for prov, loc, wd, s, e in schedules:
            if wd != day.isoweekday():
                continue
            offered = [t for t in providers[prov]["appointment_type_ids"] if t in types]
            if not offered:
                continue
            cursor = datetime.combine(day, s)
            end = datetime.combine(day, e)
            while cursor < end:
                t = types[rng.choice(offered)]
                stop = cursor + timedelta(minutes=t["duration_min"])
                if stop > end:
                    break
                if rng.random() < rate:
                    out.append((prov, loc, t["id"], cursor, stop))
                    cursor = stop
                else:
                    cursor += timedelta(minutes=30)
    return out


def free_slots(schedules: list, bookings: list, provider_id: str, location_id: str,
               duration_min: int, now: datetime, horizon_days: int = HORIZON_DAYS) -> list:
    """Reference slot computation: block starts stepped by the type's duration, minus any
    overlap with the provider's bookings (at any location), from `now` on."""
    busy = [(b_start, b_end) for prov, _, _, b_start, b_end in bookings if prov == provider_id]
    step = timedelta(minutes=duration_min)
    out = []
    for day in _iter_days(now.date(), horizon_days + 1):
        for prov, loc, wd, s, e in schedules:
            if prov != provider_id or loc != location_id or wd != day.isoweekday():
                continue
            cursor, end = datetime.combine(day, s), datetime.combine(day, e)
            while cursor + step <= end:
                if cursor >= now and not any(cursor < be and cursor + step > bs for bs, be in busy):
                    out.append(cursor)
                cursor += step
    return sorted(out)
