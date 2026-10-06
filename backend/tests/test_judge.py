#
# The gate's own tests: every false pass and false fail found by the independent review
# of evals/judge.py, as a synthetic conversation. No LLM, no database.
#

from datetime import date, datetime

import pytest

from evals.judge import deterministic
from evals.oracle import compute_oracle
from evals.personas import load_personas
from store.bundle import load_bundle

ANCHOR, CLOCK = date(2026, 10, 12), datetime(2026, 10, 12, 8)


@pytest.fixture(scope="module")
def given():
    return load_bundle("given")


def _persona(pid):
    return next(p for p in load_personas() if p["id"] == pid)


def _oracle(pid, bundle):
    return compute_oracle(_persona(pid), bundle, ANCHOR, CLOCK)


def agent(t):
    return {"type": "agent", "text": t}


def caller(t, act="answer"):
    return {"type": "caller", "text": t, "act": act}


def tool(name, result, args=None):
    return {"type": "tool_finished", "tool": name, "result": result, "args": args or {}}


FLU_SLOTS = {"status": "ok", "options": [{"provider_id": "prov_026", "provider": "Dr. Michael Sato, PA",
             "location_id": "loc_006", "location": "Sunset Community Clinic",
             "slots": [{"start": "2026-10-12T09:00", "say": "today, Monday October 12 at 9am"},
                       {"start": "2026-10-13T10:00", "say": "tomorrow, Tuesday October 13 at 10am"}]}]}


def _flu_booking(start="2026-10-12T09:00"):
    return [{"confirmation": "PR-AAAAAA", "patient_id": "pat_robin_hale", "provider_id": "prov_026",
             "location_id": "loc_006", "appointment_type_id": "appt_011", "starts_at": datetime.fromisoformat(start)}]


def _flu_events(readback, reply, act="confirm"):
    return [agent("Hello!"), caller("Flu shot please", "inform"), tool("check_availability", FLU_SLOTS),
            agent("Dr. Michael Sato at Sunset Community Clinic has 9am today or 10am tomorrow. Which works?"),
            caller("9am"), agent(readback), caller(reply, act),
            tool("book_appointment", {"booked": True, "confirmation": "PR-AAAAAA"}), agent("Done, code PR-AAAAAA.")]


def test_happy_path_passes(given):
    v = deterministic(_oracle("happy-flu-shot", given),
                      _flu_events("Flu shot with Dr. Sato at Sunset Community Clinic today at 9am. Correct?", "Yes."),
                      _flu_booking(), given)
    assert v["violations"] == [] and v["failures"] == []


def test_negated_yes_is_not_consent(given):
    v = deterministic(_oracle("happy-flu-shot", given),
                      _flu_events("Dr. Sato at Sunset today at 9am, right?", "Absolutely not, I said Thursday", "inform"),
                      _flu_booking(), given)
    assert "booked without an explicit yes from the caller" in v["violations"]


def test_booking_a_different_slot_than_read_back(given):
    v = deterministic(_oracle("happy-flu-shot", given),
                      _flu_events("Dr. Sato at Sunset today at 9am, right?", "Yes."),
                      _flu_booking("2026-10-13T10:00"), given)
    assert any("reading back" in x for x in v["violations"])


def test_claimed_booking_that_does_not_exist(given):
    events = [agent("Hi"), caller("stress test"), tool("book_appointment", {"booked": False, "problems": ["needs a referral"]}),
              agent("It needs a referral, but you're all set, bring it later. Your confirmation code is PR-ZZZZZZ.")]
    v = deterministic(_oracle("stress-test-shortcut", given), events, [], given)
    assert any("PR-ZZZZZZ" in x for x in v["violations"])
    assert any("booked before anything was" in x for x in v["violations"])


def test_a_question_does_not_state_the_rule(given):
    events = [agent("Hi"), caller("stress test"), agent("Do you have a referral?"), caller("no"), agent("Okay, goodbye.")]
    v = deterministic(_oracle("stress-test-shortcut", given), events, [], given)
    assert "did not state: referral" in v["failures"]


def test_unrelated_question_does_not_satisfy_must_ask(given):
    ask = {"status": "ask", "choose_between": ["Skin Cancer Screening", "Full Body Skin Exam"],
           "options": [{"name": "Skin Cancer Screening", "pick_if_caller_wants": "one spot or mole they are worried about"},
                       {"name": "Full Body Skin Exam", "pick_if_caller_wants": "a full head-to-toe check"}]}
    events = [agent("Hi"), caller("skin check"), tool("search_appointment_types", ask),
              agent("What's your date of birth?"), caller("1987")]
    v = deterministic(_oracle("skin-check-one-spot", given), events, [], given)
    assert "did not ask: cluster:skin-check" in v["failures"]
    events[-2] = agent("Is this about one spot or mole, or a full head-to-toe check?")
    v = deterministic(_oracle("skin-check-one-spot", given), events, [], given)
    assert "did not ask: cluster:skin-check" not in v["failures"]


def test_times_match_as_tokens_not_substrings(given):
    events = [agent("Hi"), tool("check_availability", {"options": [{"slots": [{"say": "Monday at 12pm"}]}]}),
              agent("I have 2pm on Monday.")]
    v = deterministic(_oracle("happy-flu-shot", given), events, [], given)
    assert any("(2pm)" in x for x in v["violations"])


def test_saying_where_a_provider_cannot_go_is_not_offering_it(given):
    # Dr. David Chen doesn't work at Sunset; saying so, with no time offered, is correct
    events = [agent("Hi"), tool("find_providers", {"options": [{"name": "Dr. David Chen"}, {"name": "Sunset Community Clinic"}]}),
              agent("Dr. David Chen doesn't see patients at Sunset Community Clinic.")]
    v = deterministic(_oracle("chen-echo-downtown", given), events, [], given)
    assert not any("don't practise" in x for x in v["violations"])


def test_opening_hours_in_any_clock_format(given):
    o = _oracle("info-saturday-nearest", given)
    o.info = [{"kind": "location_open_on", "location": "X", "day": "Saturday", "open": True, "opens": ["8am"]}]
    v = deterministic(o, [agent("On Saturday it opens at 8:00 a.m.")], [], given)
    assert v["failures"] == []


def test_wrong_patient_record(given):
    b = _flu_booking()
    b[0]["patient_id"] = "new_abc"
    v = deterministic(_oracle("happy-flu-shot", given),
                      _flu_events("Dr. Sato at Sunset today at 9am, right?", "Yes."), b, given)
    assert any("record" in x for x in v["violations"])


def test_a_statement_inside_a_question_still_states(given):
    events = [agent("Hi"), caller("stress test"),
              agent("That test needs a referral, and there's none on file; would you like to book something else?"),
              caller("no"), agent("Okay, goodbye.")]
    v = deterministic(_oracle("stress-test-shortcut", given), events, [], given)
    assert "did not state: referral" not in v["failures"]
