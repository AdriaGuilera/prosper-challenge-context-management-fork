#
# Tool contract over the given catalog: bounded results, the commit gate (policies
# 1-6 + slot free), server-owned idempotency, and the disambiguation verdicts.
#

import uuid
from datetime import datetime

import pytest

from agent_builder.tools import TOOLS, ToolContext, run_tool

CLOCK = datetime(2026, 10, 12, 8, 0)


def _ctx(**kw):
    return ToolContext(catalog="given", scope=f"test-{uuid.uuid4().hex[:8]}", clock=lambda: CLOCK,
                       use_vectors=False, **kw)


@pytest.fixture(autouse=True)
def _need_given(ingested):
    if "given" not in ingested:
        pytest.skip("given catalog not ingested")


async def _cleanup(conn, ctx):
    await conn.execute("DELETE FROM appointments WHERE scope = $1", ctx.scope)
    await conn.execute("DELETE FROM patients WHERE scope = $1", ctx.scope)


async def test_every_result_is_bounded():
    ctx = _ctx()
    for name, args in [("find_providers", {"specialty": "Internal Medicine"}),
                       ("find_locations", {}), ("search_appointment_types", {"query": "follow up"})]:
        r = await run_tool(name, args, ctx)
        assert {"total", "shown", "truncated", "options"} <= set(r), name
        assert r["shown"] == len(r["options"]) <= 5
        assert r["truncated"] == (r["total"] > r["shown"])


async def test_surname_collision_asks():
    r = await run_tool("find_providers", {"name": "Dr. Nguyen"}, _ctx())
    assert r["status"] == "ask" and r["total"] == 8 and r["truncated"]
    assert "specialty" in r["distinguish_by"]


async def test_misheard_surname_still_found():
    r = await run_tool("find_providers", {"name": "Dr. Ngyuen"}, _ctx())
    assert r["total"] == 8


async def test_registered_cluster_asks_in_caller_words():
    r = await run_tool("search_appointment_types", {"query": "I'd like a skin check"}, _ctx())
    assert r["status"] == "ask"
    assert set(r["choose_between"]) == {"Skin Cancer Screening", "Full Body Skin Exam"}
    assert "spot" in r["question"] and r["safer_default"] == "Full Body Skin Exam"


async def test_saying_the_exact_name_is_a_choice():
    r = await run_tool("search_appointment_types", {"query": "a full body skin exam please"}, _ctx())
    assert r["status"] == "match" and r["options"][0]["name"] == "Full Body Skin Exam"


async def test_orphan_type_is_not_offered():
    r = await run_tool("search_appointment_types", {"query": "eye doctor"}, _ctx())
    assert r["status"] == "not_offered"


async def test_place_tie_and_far_and_street():
    ctx = _ctx()
    tie = await run_tool("find_locations", {"near": "Lindenwood"}, ctx)
    assert "equally close" in tie["note"]
    far = await run_tool("find_locations", {"near": "Pinecrest"}, ctx)
    assert "Nothing near" in far["note"]
    street = await run_tool("find_locations", {"near": "Geary Blvd"}, ctx)
    assert street["status"] == "ask_place" and set(street["areas"]) == {"Downtown", "Sunset"}


async def test_booking_gate(conn):
    ctx = _ctx()
    try:
        # no patient yet
        r = await run_tool("book_appointment", {"appointment_type_id": "appt_021", "provider_id": "prov_000",
                                                "location_id": "loc_001", "start": "2026-10-12T08:00"}, ctx)
        assert r["booked"] is False
        await run_tool("lookup_patient", {"full_name": "Alan Reyes", "date_of_birth": "1980-04-02"}, ctx)
        # policy 6: Dr. David Chen works at four sites and none was offered yet
        r = await run_tool("book_appointment", {"appointment_type_id": "appt_021", "provider_id": "prov_000",
                                                "location_id": "loc_001", "start": "2026-10-12T08:00"}, ctx)
        assert r["booked"] is False and r["policies"] == ["6"] and "several locations" in r["problems"][0]
        # invented time with a one-site provider: no policy is broken, but the time was never offered
        r = await run_tool("book_appointment", {"appointment_type_id": "appt_001", "provider_id": "prov_002",
                                                "location_id": "loc_000", "start": "2026-10-12T08:00"}, ctx)
        assert r["booked"] is False and "weren't offered" in r["problems"][0]
        avail = await run_tool("check_availability", {"appointment_type_id": "appt_021", "provider_id": "prov_000"}, ctx)
        assert avail["instruction"].startswith("This provider works at several")
        opt = avail["options"][0]
        args = {"appointment_type_id": "appt_021", "provider_id": opt["provider_id"],
                "location_id": opt["location_id"], "start": opt["slots"][0]["start"]}
        first = await run_tool("book_appointment", args, ctx)
        again = await run_tool("book_appointment", args, ctx)
        assert first["booked"] and again["already_booked"] and again["confirmation"] == first["confirmation"]
        # capability: echo at a site without imaging is refused even if a slot is claimed
        r = await run_tool("book_appointment", {**args, "location_id": "loc_000"}, ctx)
        assert r["booked"] is False
        # the booked slot is gone for everyone in this scope
        avail2 = await run_tool("check_availability", {"appointment_type_id": "appt_021", "provider_id": opt["provider_id"],
                                                       "location_id": opt["location_id"]}, ctx)
        assert args["start"] not in [s["start"] for o in avail2["options"] for s in o["slots"]]
    finally:
        await _cleanup(conn, ctx)


async def test_referral_and_new_patient_gates(conn):
    ctx = _ctx()
    await run_tool("lookup_patient", {"full_name": "Maria Lopez", "date_of_birth": "1975-09-15"}, ctx)
    r = await run_tool("check_availability", {"appointment_type_id": "appt_021"}, ctx)
    assert r["status"] == "not_bookable" and "referral" in r["reasons"][0]

    ctx = _ctx()
    r = await run_tool("lookup_patient", {"full_name": "Brand New Person", "date_of_birth": "1990-01-01"}, ctx)
    assert r["patient_status"] == "new"
    r = await run_tool("check_availability", {"appointment_type_id": "appt_059", "provider_id": "prov_006"}, ctx)
    assert r["status"] == "not_bookable"  # Dr. Fatima Smith is not accepting new patients
    r = await run_tool("check_availability", {"appointment_type_id": "appt_004"}, ctx)
    assert r["status"] == "not_bookable" and "new patients" in r["reasons"][0]


def test_model_never_sets_limits_or_keys():
    for t in TOOLS.values():
        for forbidden in ("limit", "idempotency_key", "catalog_version", "is_new_patient", "has_referral"):
            assert forbidden not in t.properties, (t.name, forbidden)
