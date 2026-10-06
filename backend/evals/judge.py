#
# Two judges, one gate (persona-design §7).
#
#   deterministic  pure code over the event log + the appointments table + the oracle.
#                  It IS the pass/fail gate: global invariants, graded outcome tier,
#                  required questions, required statements, information answers.
#   llm_quality    a rubric score for how the call sounded. Reported next to the gate,
#                  never allowed to overturn it.
#

import json
import re

from store.bundle import Bundle, normalize
from store.policies import triple_problems

from . import llm
from .oracle import TIERS, Oracle, grade_booking

STATE_PATTERNS = {
    "referral": r"referr",
    "not_offered": r"(don'?t|do not|doesn'?t|does not|not)\s+(currently\s+)?(offer|have|provide|do|carry)|"
                   r"not (available|offered)|unable to (book|schedule)|can'?t (book|schedule)|no (one|provider|doctor)s? (here|at)",
    "not_accepting_new": r"(not|isn'?t|aren'?t|n'?t)\s+(currently\s+)?(accepting|taking|seeing)\s+new",
    "new_patient": r"new patient",
    "far": r"(nothing|no (clinic|location|office|site)s?|none of our|don'?t have any)[^.?!]{0,60}(near|close)|"
           r"closest|nearest|\d+\s*(km|kilomet|mile)|quite far|far from|a bit of a (drive|trip)",
    "no_provider": r"(no|n'?t (have|find|see))[^.?!]{0,40}(doctor|provider|one)[^.?!]{0,20}(by|named|called|with that)|"
                   r"couldn'?t find|can'?t find|don'?t (see|have) (a|any) (doctor|provider)",
    "provider_cannot": r"(doesn'?t|does not|can'?t|cannot|isn'?t able|is not able|n'?t)[^.?!]{0,50}(offer|do|perform|provide|see|book)",
    "cannot_book": r"can'?t (book|schedule)|unable to|not able to|isn'?t (possible|available)",
}
CLOSED = r"closed|not open|aren'?t open|isn'?t open|none of (our|the) (clinics|locations)|no (clinic|location)s? (is|are) open|only open (on )?(weekdays|monday)"
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
YES_RE = re.compile(r"\s*(yes|yeah|yep|yup|sure|correct|that'?s (right|correct|perfect|great)|sounds (good|great|right)|"
                    r"perfect|please do|go ahead|book it|absolutely|definitely|ok(ay)?[,.! ]+(book|go|that))", re.I)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", re.I)


def _norm_time(h: str, m: str, ap: str) -> str:
    ap = ap.lower().replace(".", "")
    return f"{int(h)}{':' + m if m and m != '00' else ''}{ap}"


def _texts(events: list, kind: str) -> list:
    return [e for e in events if e["type"] == kind]


def deterministic(o: Oracle, events: list, bookings: list, bundle: Bundle) -> dict:
    violations, failures = [], []
    cat = bundle.catalog

    # ---- invariants ----------------------------------------------------------------------
    for b in bookings:
        problems = triple_problems(cat, b["provider_id"], b["location_id"], b["appointment_type_id"], patient=o.patient)
        if problems:
            violations.append(f"invalid booking {b['confirmation']}: {problems}")
    if len(bookings) > 1:
        violations.append(f"{len(bookings)} bookings made in one call")

    last_act, last_text = None, ""
    seen = []  # caller words + tool results so far: what the agent may legitimately say
    for e in events:
        if e["type"] == "context":
            seen.append(e["text"])  # instructions the agent was given (the baseline's catalog lives here)
        elif e["type"] == "caller":
            last_act, last_text = e["act"], e["text"]
            seen.append(e["text"])
        elif e["type"] == "tool_finished":
            seen.append(json.dumps(e.get("result"), default=str))
            if e["tool"] == "book_appointment" and (e.get("result") or {}).get("booked") \
                    and not (e.get("result") or {}).get("already_booked") and last_act != "confirm" \
                    and not YES_RE.match(last_text):
                violations.append("booked without an explicit yes from the caller")
        elif e["type"] == "agent":
            corpus = normalize(" ".join(seen))
            said = normalize(e["text"])
            for p in cat["providers"]:
                full = normalize(p["name"].replace("Dr.", ""))
                if f" {full} " in f" {said} " and f" {full} " not in f" {corpus} ":
                    violations.append(f"named provider '{p['name']}' before any lookup returned it")
            for l in cat["locations"]:
                if f" {normalize(l['name'])} " in f" {said} " and f" {normalize(l['name'])} " not in f" {corpus} ":
                    violations.append(f"named location '{l['name']}' before any lookup returned it")
            # a provider offered at a site they don't practise at (system-agnostic: holds even
            # when the whole catalog sits in the prompt)
            named_p = [p for p in cat["providers"]
                       if f" {normalize(p['name'].replace('Dr.', ''))} " in f" {said} "]
            named_l = [l for l in cat["locations"] if f" {normalize(l['name'])} " in f" {said} "]
            if len(named_p) == 1:
                for l in named_l:
                    if l["id"] not in named_p[0]["location_ids"]:
                        violations.append(f"offered {named_p[0]['name']} at {l['name']}, where they don't practise")
            raw_seen = " ".join(seen).lower().replace(" ", "").replace(".", "")
            for h, m, ap in TIME_RE.findall(e["text"]):
                t = _norm_time(h, m, ap)
                if t not in raw_seen and t.replace(":00", "") not in raw_seen:
                    violations.append(f"spoke a time ({t}) no lookup returned")
    violations = list(dict.fromkeys(violations))

    # ---- outcome -------------------------------------------------------------------------
    final = bookings[-1] if bookings else None
    tier, tier_reasons = grade_booking(o, final, bundle)

    # ---- required questions ----------------------------------------------------------------
    agent_after = lambda idx: [e for e in events[idx + 1:] if e["type"] == "agent"]  # noqa: E731
    for ask in o.must_ask:
        want = {"provider": ("find_providers", "ask"), "place": (None, "ask_place")}.get(ask, ("search_appointment_types", "ask"))
        ok = False
        for i, e in enumerate(events):
            r = e.get("result") or {}
            ambiguous_arg = ask in ("provider", "place") and r.get("status") == "ask" and r.get("ambiguous_arg")
            if e["type"] == "tool_finished" and (ambiguous_arg or (
                    (want[0] is None or e["tool"] == want[0]) and r.get("status") == want[1])):
                nxt = agent_after(i)
                if nxt and "?" in nxt[0]["text"]:
                    ok = True
                    break
        if not ok:
            # same standard for every system: a question to the caller that names at least two
            # of the things being disambiguated (two first names, two visit types, two areas)
            labels = o.ask_labels.get(ask, [])
            for e in events:
                if e["type"] == "agent" and "?" in e["text"]:
                    low = e["text"].lower()
                    if sum(1 for lab in labels if lab and re.search(rf"\b{re.escape(lab)}\b", low)) >= 2:
                        ok = True
                        break
                    says = o.ask_labels.get("provider_says")
                    if ask == "provider" and says and says in low and re.search(
                            r"\b(which|two|three|several|multiple|more than one|a few|both)\b", low):
                        ok = True
                        break
        if not ok:
            failures.append(f"did not ask: {ask}")

    # ---- required statements -----------------------------------------------------------------
    agent_text = " ".join(e["text"] for e in events if e["type"] == "agent")
    for st in o.must_state:
        if not re.search(STATE_PATTERNS.get(st, st), agent_text, re.I):
            failures.append(f"did not state: {st}")

    # ---- information answers ------------------------------------------------------------------
    low = agent_text.lower()
    for item in o.info:
        if item["kind"] == "open_on":
            ok = re.search(CLOSED, low) if not item["open_locations"] else any(
                n.lower() in low for n in item["open_locations"])
            if not ok:
                failures.append(f"wrong or missing answer: open on {item['day']}")
        elif item["kind"] == "location_open_on":
            squashed = low.replace(" ", "").replace(".", "")
            ok = any(t in squashed for t in item["opens"]) if item["open"] else re.search(CLOSED, low)
            if not ok:
                failures.append(f"wrong or missing answer: {item['location']} on {item['day']}")
        elif item["kind"] == "nearest_to":
            if not any(n.lower() in low for n in item["locations"]):
                failures.append(f"did not name the nearest location to {item['place']}")
        elif item["kind"] == "count":
            n = item["count"]
            words = [str(n)] + ([NUMBER_WORDS[n]] if n < len(NUMBER_WORDS) else [])
            if not any(re.search(rf"\b{w}\b", low) for w in words):
                failures.append(f"did not give the count ({n})")
        elif item["kind"] == "any_provider":
            if item["providers"]:
                if not any(normalize(p).split()[-1] in normalize(low).split() for p in item["providers"]):
                    failures.append("did not name a provider matching the question")
            elif not re.search(r"(no|none|n'?t)", low):
                failures.append("did not say no provider matches")

    passed = not violations and not failures and TIERS[tier] >= TIERS["acceptable"]
    return {
        "pass": passed, "tier": tier, "score": TIERS[tier] if not violations else 0.0,
        "tier_reasons": tier_reasons, "violations": violations, "failures": failures,
        "booking": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in final.items()} if final else None,
    }


# ---- LLM quality judge ---------------------------------------------------------------------

JUDGE_MODEL = "gpt-4o"
RUBRIC = """You grade the QUALITY of a phone scheduling assistant's side of a call (not whether the booking was correct; that is graded elsewhere).
Score each 1-5:
- clarity: easy to follow, one thing at a time
- voice_fit: short spoken sentences, no lists, no ids or jargon read aloud
- questions: when it asked, were the questions answerable by a patient, in plain words (not catalog labels)?
- efficiency: no needless repetition or re-asking; got to times promptly
- recovery: handled corrections, refusals and pushback gracefully (5 if none occurred)
Reply ONLY with JSON: {"clarity": n, "voice_fit": n, "questions": n, "efficiency": n, "recovery": n, "comment": "<one sentence>"}"""


async def llm_quality(events: list, meter=None) -> dict:
    lines = [f"{'Caller' if e['type'] == 'caller' else 'Assistant'}: {e['text']}"
             for e in events if e["type"] in ("caller", "agent")]
    msg = await llm.chat(JUDGE_MODEL, [{"role": "system", "content": RUBRIC},
                                       {"role": "user", "content": "\n".join(lines)}],
                         meter=meter, role="judge", temperature=0, response_format={"type": "json_object"})
    try:
        out = json.loads(msg.content)
        keys = ["clarity", "voice_fit", "questions", "efficiency", "recovery"]
        out["mean"] = round(sum(float(out[k]) for k in keys) / len(keys), 2)
        return out
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"error": "unparseable judge output"}
