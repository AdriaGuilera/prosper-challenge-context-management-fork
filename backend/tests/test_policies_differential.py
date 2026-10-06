#
# The SQL `bookable` relation vs the independent Python reference, on every raw
# (provider, location, type) triple, for every ingested catalog and a spread of
# patient states. This is what licenses SQL to hold policies 1-5 (decision 001).
#

import random

import pytest

from store.bundle import load_bundle
from store.policies import PatientState, raw_triples, valid_triples


async def _sql_structural(conn, cv):
    rows = await conn.fetch("SELECT provider_id, location_id, appointment_type_id FROM bookable WHERE catalog_version = $1", cv)
    return {tuple(r) for r in rows}


async def _sql_for(conn, cv, patient: PatientState):
    rows = await conn.fetch("SELECT * FROM bookable_for($1, $2, $3)", cv, patient.is_new, sorted(patient.referral_type_ids))
    return {tuple(r) for r in rows}


async def test_given_catalog_counts(conn, ingested):
    if "given" not in ingested:
        pytest.skip("given catalog not ingested")
    bundle = load_bundle("given")
    raw = raw_triples(bundle.catalog)
    py_valid = valid_triples(bundle.catalog)
    sql_valid = await _sql_structural(conn, ingested["given"][0])
    assert (len(raw), len(py_valid), len(raw) - len(py_valid)) == (797, 760, 37)
    assert sql_valid == py_valid


async def test_every_catalog_agrees(conn, ingested):
    checked = 0
    for name, (cv, _) in ingested.items():
        bundle = load_bundle(name)
        assert bundle.version == cv, f"{name}: bundle on disk changed since ingest; re-run `make ingest`"
        assert await _sql_structural(conn, cv) == valid_triples(bundle.catalog), name

        referral_types = sorted(t["id"] for t in bundle.catalog["appointment_types"] if t["requires_referral"])
        rng = random.Random(13)
        half = frozenset(rng.sample(referral_types, len(referral_types) // 2)) if referral_types else frozenset()
        for is_new in (False, True):
            for refs in (frozenset(), frozenset(referral_types), half):
                patient = PatientState(is_new, refs)
                sql = await _sql_for(conn, cv, patient)
                py = valid_triples(bundle.catalog, patient)
                assert sql == py, f"{name} is_new={is_new} refs={len(refs)}: {len(sql ^ py)} triples disagree"
                checked += 1
    assert checked >= 6


class _Rollback(Exception):
    pass


async def test_specialty_is_never_an_eligibility_key(conn, ingested):
    """Scramble every specialty label: the relation must not move by a single triple."""
    for name, (cv, _) in ingested.items():
        before = await _sql_structural(conn, cv)
        try:
            async with conn.transaction():
                await conn.execute("UPDATE providers SET specialty = 'x' || id WHERE catalog_version = $1", cv)
                await conn.execute("UPDATE appointment_types SET specialty = 'x' || id WHERE catalog_version = $1", cv)
                assert await _sql_structural(conn, cv) == before, name
                raise _Rollback
        except _Rollback:
            pass


async def test_absent_capability_is_null(conn, ingested):
    for name, (cv, _) in ingested.items():
        bundle = load_bundle(name)
        absent = {t["id"] for t in bundle.catalog["appointment_types"] if not t.get("required_capability")}
        nulls = {r[0] for r in await conn.fetch(
            "SELECT id FROM appointment_types WHERE catalog_version = $1 AND required_capability IS NULL", cv)}
        assert absent == nulls, name
