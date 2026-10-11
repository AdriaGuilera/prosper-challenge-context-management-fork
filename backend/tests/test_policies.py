#
# The policy functions one by one, and PolicySet as the tools use it. No database: the
# catalog payload is enough. (That the set agrees with the SQL relation on every triple is
# test_policies_differential.py.)
#

import pytest

from store.bundle import load_bundle
from store.policies import POLICIES, Candidate, Conversation, PatientState, PolicySet

EXISTING = PatientState(is_new=False)
NEW = PatientState(is_new=True)


@pytest.fixture(scope="module")
def ps():
    return PolicySet(load_bundle("given").catalog)


def _codes(ps, c, patient=None, conv=None):
    return [v.policy for v in ps.violations(c, patient, conv)]


def test_one_function_per_catalog_policy(ps):
    """Every policy the catalog states has exactly one function, and the numbering is stable."""
    assert len(ps.facts.catalog["policies"]) == 6
    assert [p.number for p in POLICIES] == ["1", "2", "3", "4", "5a", "5b", "6"]
    assert len({p.check for p in POLICIES}) == len(POLICIES)
    assert ps.by_number("4").check.__name__ == "referral_on_file"
    assert ps.by_number("4").text.startswith("A type with requires_referral")


def test_policy_1_provider_at_location(ps):
    # Dr. Maria Garcia works only at loc_000
    assert _codes(ps, Candidate("prov_002", "loc_006", "appt_001")) == ["1"]
    assert _codes(ps, Candidate("prov_002", "loc_000", "appt_001")) == []


def test_policy_2_provider_offers_type(ps):
    assert "2" in _codes(ps, Candidate("prov_002", "loc_000", "appt_021"))


def test_policy_3_capability(ps):
    # echocardiogram needs imaging; Dr. David Chen works at Mission Bay (no imaging) and Downtown (imaging)
    assert _codes(ps, Candidate("prov_000", "loc_000", "appt_021")) == ["3"]
    assert _codes(ps, Candidate("prov_000", "loc_004", "appt_021")) == []


def test_policy_4_referral(ps):
    c = Candidate("prov_000", "loc_004", "appt_021")
    assert _codes(ps, c, EXISTING) == ["4"]
    assert _codes(ps, c, PatientState(False, frozenset({"appt_021"}))) == []


def test_policy_5_new_patients(ps):
    assert "5a" in _codes(ps, Candidate(appointment_type_id="appt_004"), NEW)
    assert _codes(ps, Candidate(provider_id="prov_000"), NEW) == ["5b"]       # David Chen isn't accepting new patients
    assert _codes(ps, Candidate(provider_id="prov_000"), EXISTING) == []


def test_policy_6_location_settled(ps):
    c = Candidate("prov_000", "loc_004", "appt_021")
    refs = PatientState(False, frozenset({"appt_021"}))
    assert _codes(ps, c, refs, Conversation()) == ["6"]
    assert _codes(ps, c, refs, Conversation({"prov_000": {"loc_004"}})) == []
    # one site only: nothing to settle
    assert _codes(ps, Candidate("prov_002", "loc_000", "appt_001"), EXISTING, Conversation()) == []


def test_partial_candidates_skip_what_they_cannot_check(ps):
    # a type alone: only type-level policies can run
    assert _codes(ps, Candidate(appointment_type_id="appt_021")) == []
    assert _codes(ps, Candidate(appointment_type_id="appt_021"), EXISTING) == ["4"]
    # no conversation passed: policy 6 is not evaluated
    assert _codes(ps, Candidate("prov_000", "loc_004", "appt_021"), PatientState(False, frozenset({"appt_021"}))) == []


def test_unknown_ids_are_refused(ps):
    assert [v.code for v in ps.violations(Candidate("prov_999", "loc_000", "appt_001"))] == ["unknown_entity"]


def test_a_subset_runs_only_its_policies():
    catalog = load_bundle("given").catalog
    only_catalog = PolicySet(catalog, only={"1", "2", "3"})
    assert repr(only_catalog) == "PolicySet(1, 2, 3)"
    c = Candidate("prov_000", "loc_004", "appt_021")
    assert only_catalog.violations(c, NEW, Conversation()) == []
    assert {v.policy for v in PolicySet(catalog).violations(c, NEW, Conversation())} == {"4", "5a", "5b", "6"}


def test_triples_counts_and_patient_narrowing(ps):
    assert (len(ps.raw_triples()), len(ps.triples())) == (797, 760)
    assert ps.triples(NEW) < ps.triples(EXISTING) <= ps.triples()


def test_reasons_come_from_the_blocking_policy(ps):
    assert "referral" in ps.explain_none("appt_021", patient=EXISTING)[0]
    assert ps.explain_none("appt_021", provider_id="prov_002") == ["That provider can't do Echocardiogram."]
    assert "new patients" in ps.explain_none("appt_004", patient=NEW)[0]
