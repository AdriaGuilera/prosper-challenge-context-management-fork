#
# Slots computed in SQL (what the tools use) vs the Python reference over a
# calendar regenerated from the seed: proves ingest, the generator and the SQL
# slot arithmetic agree.
#

import random
from datetime import datetime, time

from store import calendar
from store.bundle import load_bundle
from store.policies import valid_triples
from store.slots import free_slots


async def test_sql_slots_match_reference(conn, ingested):
    for name, (cv, anchor) in ingested.items():
        bundle = load_bundle(name)
        cat = bundle.catalog
        schedules = calendar.generate_schedules(cat, bundle.seed, bundle.calendar.get("density", "normal"))
        bookings = calendar.generate_prebookings(cat, schedules, bundle.seed, anchor, bundle.calendar.get("prebook_rate", 0.3))
        types = bundle.types
        triples = sorted(valid_triples(cat))
        rng = random.Random(5)
        compared = 0
        for prov, loc, tid in rng.sample(triples, min(25, len(triples))):
            now = datetime.combine(anchor, time(8)) .replace(hour=rng.choice([8, 11, 15]))
            dur = types[tid]["duration_min"]
            ref = calendar.free_slots(schedules, bookings, prov, loc, dur, now)
            got = await free_slots(conn, cv, [(prov, loc)], dur, now, ["base"], per_pair=10_000)
            assert [s for _, _, s in got] == ref, f"{name} {prov} {loc} {tid}"
            compared += len(ref)
        assert compared > 100, f"{name}: only {compared} slots compared"


async def test_no_provider_double_booked_by_schedule(conn, ingested):
    for name, (cv, _) in ingested.items():
        clash = await conn.fetchval(
            """SELECT count(*) FROM schedules a JOIN schedules b
               ON a.catalog_version = b.catalog_version AND a.provider_id = b.provider_id AND a.weekday = b.weekday
              AND (a.location_id, a.starts) < (b.location_id, b.starts) AND a.starts < b.ends AND b.starts < a.ends
               WHERE a.catalog_version = $1""", cv)
        assert clash == 0, name


async def test_schedules_inside_location_hours(conn, ingested):
    for name, (cv, _) in ingested.items():
        outside = await conn.fetchval(
            """SELECT count(*) FROM schedules s WHERE s.catalog_version = $1 AND NOT EXISTS (
                 SELECT 1 FROM location_hours h WHERE h.catalog_version = s.catalog_version
                   AND h.location_id = s.location_id AND h.weekday = s.weekday
                   AND h.opens <= s.starts AND s.ends <= h.closes)""", cv)
        assert outside == 0, name
