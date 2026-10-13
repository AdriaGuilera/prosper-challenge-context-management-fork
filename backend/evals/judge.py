#
# The judge: one LLM call per conversation decides pass/fail and how the call sounded.
#
# It reads the whole call (every caller and assistant line, every tool call and result, in
# order), the bookings actually written to the appointments table, and an answer key built
# from the oracle in names, not ids. The oracle stays code: it is the ground truth for what
# this caller should get in this catalog. Every judgement against it (invariants, outcome
# tier, required questions and statements, information answers) is the LLM's.
#
# The verdict has the same shape the runner, the report and results.jsonl always used.
# `pass` is combined from the LLM's fields by a fixed rule (no violation, no failure,
# tier at least acceptable); that is arithmetic, not a judgement.
#

import json
from datetime import datetime
from typing import Optional

from store.bundle import Bundle
from store.policies import triple_problems

from . import llm
from .oracle import TIERS, Oracle

JUDGE_MODEL = "gpt-5.6-terra"   # chosen on agreement with hand-checked verdicts
MAX_RESULT_CHARS = 1500

STATE_MEANING = {
    "referral": "the visit needs a referral and none is on file",
    "not_offered": "the clinic doesn't offer that visit",
    "not_accepting_new": "the provider or visit isn't open to new patients",
    "new_patient": "the caller is a new patient, which is why it can't be booked",
    "far": "nothing is near the place the caller named (and which is the closest)",
    "no_provider": "there is no provider by the name the caller gave",
    "provider_cannot": "the provider the caller named doesn't do that visit (there or at all)",
    "cannot_book": "the request can't be booked",
}

RUBRIC = """You are the judge of an automated evaluation of a phone scheduling assistant for a medical clinic.
A simulated caller talked to the assistant. You get the ANSWER KEY (what correct looks like for this caller in this
clinic), the TRANSCRIPT (every caller and assistant line, plus every tool the assistant called and what it returned,
in order), and the BOOKINGS actually written to the clinic's database during the call.

Judge strictly from this evidence. The database is the truth about what was booked, whatever the assistant said.
"A booking" always means a book_appointment call that SUCCEEDED ("booked": true). A rejected attempt is not a
booking: "before booking" means before the first successful one, or anywhere in the call if none succeeded.

1. VIOLATIONS: list every one of these the assistant broke (one short sentence each; empty list if none):
- the required order before a booking is: the assistant reads the booking back (doctor or place, and the time) ->
  the caller says yes to THAT read-back -> the booking. A caller picking an option from a list ("the 12:40 one",
  "let's do Wednesday at Mission District") is neither the read-back nor the yes. So if a booking follows the
  caller's pick directly, report BOTH: "booked without reading back the doctor or place and the time" and "booked
  without an explicit yes". A "no", a correction, or a "yes, but..." is not a yes either.
- booked a different slot (provider, place, or time) than the one it read back
- named a provider or location before any tool result (or its own instructions, when they contain the catalog)
  showed it
- spoke a clock time for an appointment that no tool result contained
- offered a provider at a location where that provider doesn't work
- read out a confirmation code that isn't in BOOKINGS, or told the caller something was booked/confirmed before a
  booking succeeded (a read-back that ends in a question asking for confirmation is NOT a claim)
- more than one booking in the call, a booking the policy check marks invalid, or a booking on the wrong patient
  record (see the answer key)

2. OUTCOME TIER of the final booking against the answer key: ideal | acceptable | suboptimal | wrong.
- Expected "booking": no booking -> wrong. Invalid booking -> wrong. A visit type not in the key -> wrong. Its tier is
  capped by the visit type's tier in the key. A provider other than the one asked for: wrong if the key says the
  caller insisted (strict), else at most suboptimal when the preferred one was bookable. Outside the caller's stated
  preferences -> at most suboptimal. Not one of the nearest suitable locations (when the key lists them) -> at most
  acceptable. Before the caller's earliest date -> wrong. Wrong part of day -> at most suboptimal. Later than the
  soonest suitable slot: up to 2 days later -> at most acceptable; more -> at most suboptimal.
- Expected "no_booking": no booking -> ideal. A valid booking of something else the caller agreed to -> acceptable,
  but only if the assistant first said why the original request can't be booked; otherwise wrong. An invalid
  booking -> wrong.
- Expected "information": no booking -> ideal; any booking -> wrong.
Grade only against what the key states: when the key gives no soonest slot or nearest location, don't work one out
yourself. Give a short reason for anything below ideal.

3. FAILURES: list every MUST ASK, MUST STATE and INFORMATION line of the key the assistant missed (one short
sentence each; empty if none). Only those lines: the caller's goal explains the call, it adds no requirements.
- MUST ASK: before booking, the assistant asked the caller to choose, in a question that names at least two of the
  choices (with exactly two choices, a yes/no question about one of them is enough). For which provider, telling the
  caller there are several providers by that name and asking which one (by first name, specialty or location) also
  counts. Asking something else does not count.
- MUST STATE: the assistant said it as a statement, before any booking. A question ("Do you have a referral?") does
  not state it. Any one of the "any of" reasons is enough.
- INFORMATION: the assistant gave the right answer (the right count, the right locations, the right hours, or that
  none match).

4. QUALITY, each 1-5, independent of correctness: clarity (easy to follow, one thing at a time), voice_fit (short
spoken sentences, no lists, no ids or jargon read aloud), questions (answerable by a patient, in plain words),
efficiency (no needless repetition; got to times promptly), recovery (handled corrections, refusals and pushback
gracefully; 5 if none occurred).

Reply ONLY with JSON:
{"violations": [...], "tier": "...", "tier_reasons": [...], "failures": [...],
 "quality": {"clarity": n, "voice_fit": n, "questions": n, "efficiency": n, "recovery": n, "comment": "<one sentence>"}}"""


# ---- the answer key, in names ------------------------------------------------------------


def _when(dt: datetime) -> str:
    """'Wednesday October 14 at 11am' / '... at 2:45pm'."""
    clock = dt.strftime("%-I%p") if dt.minute == 0 else dt.strftime("%-I:%M%p")
    return f"{dt:%A %B %-d} at {clock.lower()}"


def answer_key(o: Oracle, bundle: Bundle, persona: dict) -> str:
    T, P, L = bundle.types, bundle.providers, bundle.locations
    c = persona.get("caller", {})
    lines = [f"Caller: {c.get('name')}, date of birth {c.get('dob')}. What they want: {c.get('goal', '').strip()}",
             f"Patient: {'NEW patient (no record)' if o.patient.is_new else 'existing patient'}; referrals on file: "
             + (", ".join(sorted(T[t]['name'] for t in o.patient.referral_type_ids if t in T)) or "none") + "."]
    if o.patient_ref == "new":
        lines.append("Correct patient record: a new record must be created (patient_id starting 'new_').")
    elif o.patient_ref:
        lines.append(f"Correct patient record(s): {', '.join(sorted(o.patient_ref))}.")
    lines.append(f"Expected outcome: {o.outcome}.")
    if o.type_tier:
        by = {}
        for t, tier in o.type_tier.items():
            by.setdefault(tier, []).append(T[t]["name"] if t in T else t)
        lines.append("Visit types by tier: " + "; ".join(f"{k}: {', '.join(sorted(v))}" for k, v in by.items()) + ".")
    if o.provider_match:
        names = sorted(P[p]["name"] for p in o.provider_match if p in P)
        lines.append(f"Provider the caller asked for: {' or '.join(names)} "
                     f"({'they insisted on this provider' if o.provider_strict else 'a preference, not a requirement'}).")
    if o.target:
        t = o.target
        lines.append(f"TARGET BOOKING (what this caller is aiming for): {t['text']}. Time: {t['time_rule']}"
                     + (f"; {t['equally_good']} other booking(s) of the same visit tier"
                        + (" at the same time" if "soonest" in t["time_rule"] else "")
                        + " are equally good" if t["equally_good"] else "") + ".")
    elif o.outcome == "no_booking":
        lines.append("TARGET BOOKING: none. The right outcome is to explain why it can't be booked, and not book it.")
    if o.constrained:
        opts = sorted({(P[p]["name"], L[l]["name"]) for p, l, _ in o.constrained})
        shown = "; ".join(f"{p} at {l}" for p, l in opts[:15]) + ("; ..." if len(opts) > 15 else "")
        lines.append(f"Bookable provider/location options within the caller's preferences ({len(opts)}): {shown}.")
    elif o.outcome == "booking":
        lines.append("Nothing matches every stated preference; any valid booking of a listed visit type is graded by the rules.")
    if o.best_locations:
        lines.append(f"Nearest suitable location(s): {', '.join(sorted(L[l]['name'] for l in o.best_locations))}.")
    if o.soonest:
        lines.append(f"Soonest suitable slot: {_when(o.soonest)}.")
    if o.on_or_after:
        lines.append(f"The caller can't come before {o.on_or_after:%A %B %-d}.")
    if o.part_of_day:
        lines.append(f"The caller wants the {o.part_of_day}.")
    for ask in o.must_ask:
        labels = o.ask_labels.get(ask, [])
        lines.append(f"MUST ASK ({ask}): which one the caller means; the choices include: {', '.join(labels[:12])}.")
    for st in o.must_state:
        reasons = [STATE_MEANING.get(s, s) for s in st.split("|")]
        lines.append("MUST STATE (any of): " + " / ".join(reasons) + ".")
    for item in o.info:
        lines.append(f"INFORMATION the caller asks for, correct answer: {json.dumps(item, default=str)}")
    for n in o.notes:
        lines.append(f"Note: {n}")
    return "\n".join(lines)


# ---- the evidence ------------------------------------------------------------------------


def transcript(events: list) -> str:
    args = {e["id"]: e.get("args") for e in events if e["type"] == "tool_started"}
    out = []
    catalog_noted = False
    for e in events:
        if e["type"] == "context" and "CLINIC CATALOG" in e.get("text", "") and not catalog_noted:
            catalog_noted = True
            out.append("[The assistant's instructions contain the clinic catalog: it may name providers and locations "
                       "from it without a lookup. Appointment times must still come from tool results.]")
        elif e["type"] == "caller":
            out.append(f"CALLER: {e['text']}")
        elif e["type"] == "agent":
            out.append(f"ASSISTANT: {e['text']}")
        elif e["type"] == "tool_finished":
            res = json.dumps(e.get("result"), default=str)
            if len(res) > MAX_RESULT_CHARS:
                res = res[:MAX_RESULT_CHARS] + " ...(truncated)"
            out.append(f"  [tool {e['tool']}({json.dumps(args.get(e.get('id')), default=str)}) -> {res}]")
        elif e["type"] == "error":
            out.append(f"[conversation error: {e['text']}]")
    return "\n".join(out)


def bookings_text(bookings: list, o: Oracle, bundle: Bundle) -> str:
    if not bookings:
        return "No bookings were written."
    T, P, L = bundle.types, bundle.providers, bundle.locations
    out = []
    for b in bookings:
        problems = triple_problems(bundle.catalog, b["provider_id"], b["location_id"], b["appointment_type_id"],
                                   patient=o.patient)
        out.append(f"{b['confirmation']}: {T.get(b['appointment_type_id'], {}).get('name', b['appointment_type_id'])} with "
                   f"{P.get(b['provider_id'], {}).get('name', b['provider_id'])} at "
                   f"{L.get(b['location_id'], {}).get('name', b['location_id'])}, {_when(b['starts_at'])}; "
                   + (f"patient_id {b['patient_id']}" if b.get("patient_id")
                      else "patient record not logged in this run (do not judge the record)")
                   + f"; policy check: {'valid' if not problems else 'INVALID ' + ', '.join(problems)}")
    return "\n".join(out)


# ---- the verdict ---------------------------------------------------------------------------


def _model_params(model: str) -> dict:
    if model.startswith("gpt-5"):
        return {"reasoning_effort": "medium"}
    return {"temperature": 0}


async def judge(o: Oracle, events: list, bookings: list, bundle: Bundle, persona: dict,
                model: str = JUDGE_MODEL, meter=None, error: Optional[str] = None) -> dict:
    user = (f"ANSWER KEY\n{answer_key(o, bundle, persona)}\n\nTRANSCRIPT\n{transcript(events)}\n\n"
            f"BOOKINGS\n{bookings_text(bookings, o, bundle)}")
    msg = await llm.chat(model, [{"role": "system", "content": RUBRIC}, {"role": "user", "content": user}],
                         meter=meter, role="judge", response_format={"type": "json_object"}, **_model_params(model))
    try:
        out = json.loads(msg.content)
        tier = out["tier"] if out.get("tier") in TIERS else "wrong"
        violations = [str(v) for v in out.get("violations") or []]
        failures = [str(f) for f in out.get("failures") or []]
        tier_reasons = [str(r) for r in out.get("tier_reasons") or []]
        quality = out.get("quality") or {}
        keys = ["clarity", "voice_fit", "questions", "efficiency", "recovery"]
        if all(k in quality for k in keys):
            quality["mean"] = round(sum(float(quality[k]) for k in keys) / len(keys), 2)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        tier, violations, failures, tier_reasons, quality = "wrong", [], ["judge output unparseable"], [], {}
    if error:
        failures.append(f"conversation error: {error}")
    final = bookings[-1] if bookings else None
    return {
        "pass": not violations and not failures and TIERS[tier] >= TIERS["acceptable"],
        "tier": tier, "score": TIERS[tier] if not violations else 0.0,
        "tier_reasons": tier_reasons, "violations": violations, "failures": failures, "quality": quality,
        "judge_model": model,
        "booking": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in final.items()} if final else None,
    }
