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
    "not_accepting_new": r"(not|isn'?t|aren'?t|n'?t)\s+(currently\s+)?(accepting|taking|seeing)\s+(any\s+)?new|panel is (full|closed)",
    "new_patient": r"new patient",
    "far": r"(nothing|no (clinic|location|office|site)s?|none of our|don'?t have any)[^.?!]{0,60}(near|close)|"
           r"closest|nearest|\d+\s*(km|kilomet|mile)|quite far|far from|a bit of a (drive|trip)",
    "no_provider": r"(no|n'?t (have|find|see))[^.?!]{0,40}(doctor|provider|one|anyone)[^.?!]{0,20}(by|named|called|with that)|"
                   r"couldn'?t find|can'?t find|not finding|don'?t (see|have) (a|any) (doctor|provider)",
    "provider_cannot": r"(doesn'?t|does not|can'?t|cannot|isn'?t able|is not able|n'?t)[^.?!]{0,50}(offer|do|perform|provide|see|book)",
    "cannot_book": r"can'?t (book|schedule)|unable to|not able to|isn'?t (possible|available)",
}
CLOSED = r"\b(no|not|n't|none)\b[^.?!]{0,50}\bopen\b|\bclosed\b|none of (our|the) (clinics|locations)"
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
YES_RE = re.compile(r"\s*(yes|yeah|yep|yup|sure|correct|that'?s (right|correct|perfect|great)|sounds (good|great|right)|"
                    r"perfect|please do|go ahead|book it|absolutely|definitely|ok(ay)?[,.! ]+(book|go|that))", re.I)
NEGATION_RE = re.compile(r"\b(no|not|n't|but|actually|wait|instead|rather)\b|n't\b", re.I)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", re.I)
CLAIMED_RE = re.compile(r"you'?re (all )?(set|booked)|(is|has been|have been|are) (now )?(booked|confirmed|scheduled)|"
                        r"i'?ve (booked|scheduled)|i have (booked|scheduled)|booking is confirmed|confirmation (code|number) is", re.I)
CODE_RE = re.compile(r"\bPR-[A-Z0-9]{6}\b")
GENERIC = {"health", "center", "clinic", "medical", "group", "specialty", "family", "community", "care", "the", "and", "of"}
STOP = {"the", "and", "for", "with", "they", "their", "about", "this", "that", "your", "you", "are", "was", "were", "been",
        "need", "want", "check", "visit", "doctor", "one", "some", "just", "more", "than", "have", "told"}
PLURAL_RE = re.compile(r"\b(two|three|four|several|multiple|more than one|a few|both|which (one|dr|doctor)|which of)\b")


def _straight(text: str) -> str:
    """Typographic quotes to ASCII: GPT-5.6 writes doesn’t, the patterns say doesn't."""
    return text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def time_tokens(text: str) -> set:
    """Clock times in any of the forms seen in tool results and speech, normalised to '9am'/'9:30pm'."""
    out = set()
    for h, m, ap in TIME_RE.findall(text):
        ap = ap.lower().replace(".", "")
        out.add(f"{int(h)}{':' + m if m and m != '00' else ''}{ap}")
    for hh, mm in re.findall(r"\b(\d{1,2}):(\d{2})\b(?!\s*[ap]\.?m)", text, re.I):
        h = int(hh)
        if h < 24:
            out.add(f"{h % 12 or 12}{':' + mm if mm != '00' else ''}{'am' if h < 12 else 'pm'}")
    return out


def _spoken_time_matches(text: str, start) -> bool:
    """Does a read-back say this start time, in digits ("9:30 AM") or in words ("nine thirty")?"""
    h12 = start.hour % 12 or 12
    want = f"{h12}{':%02d' % start.minute if start.minute else ''}{'am' if start.hour < 12 else 'pm'}"
    if want in time_tokens(text):
        return True
    return re.search(rf"\b{NUMBER_WORDS[h12]}\b", text.lower()) is not None


def _declarative(text: str) -> str:
    """Sentences that state something; questions ("Do you have a referral?") don't count as stating."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(p for p in parts if not p.strip().endswith("?"))


def _sentences(text: str, must_contain: str) -> list:
    return [p for p in re.split(r"(?<=[.!?])\s+", text) if must_contain in p.lower() and not p.strip().endswith("?")]


def _labels_named(text: str, labels: list) -> int:
    """How many distinct labels a question names: a label counts when the text has one of its
    distinctive words (not shared with any other label)."""
    low = normalize(text)
    words = [{w for w in normalize(lab).split() if len(w) >= 3 and w not in STOP} for lab in labels]
    named = 0
    for i, ws in enumerate(words):
        others = set().union(*(w for j, w in enumerate(words) if j != i)) if len(words) > 1 else set()
        distinctive = ws - others or ws
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in distinctive):
            named += 1
    return named


def _ask_labels_from(result: dict) -> list:
    """The things a tool said the caller must choose between."""
    labels = list(result.get("choose_between") or [])
    for o in result.get("options") or []:
        if o.get("pick_if_caller_wants"):
            labels.append(o["pick_if_caller_wants"])
    if result.get("ambiguous_arg") or (result.get("distinguish_by") is not None):
        for o in result.get("options") or []:
            if o.get("name"):
                labels.append(normalize(o["name"].replace("Dr.", "")).split()[0])
        labels += list(result.get("candidates") or [])
    labels += list(result.get("areas") or [])
    if result.get("status") == "ask_place":
        labels += list(result.get("candidates") or [])
    return [l for l in dict.fromkeys(labels) if l]


def deterministic(o: Oracle, events: list, bookings: list, bundle: Bundle) -> dict:
    events = [dict(e, text=_straight(e["text"])) if isinstance(e.get("text"), str) else e for e in events]
    violations, failures = [], []
    cat = bundle.catalog
    providers, locations = bundle.providers, bundle.locations
    booked_codes = {b["confirmation"] for b in bookings}

    # ---- invariants on what was written --------------------------------------------------
    for b in bookings:
        problems = triple_problems(cat, b["provider_id"], b["location_id"], b["appointment_type_id"], patient=o.patient)
        if problems:
            violations.append(f"invalid booking {b['confirmation']}: {problems}")
        pid = b.get("patient_id")
        if pid is not None and o.patient_ref is not None:
            if o.patient_ref == "new" and not str(pid).startswith("new_"):
                violations.append("booked a new caller onto someone else's record")
            elif o.patient_ref != "new" and pid != o.patient_ref:
                violations.append("booked under the wrong patient record (misheard name or date of birth)")
    if len(bookings) > 1:
        violations.append(f"{len(bookings)} bookings made in one call")

    # ---- invariants on what was said and done, in order ------------------------------------
    last_act, last_text, prev_agent, last_agent = None, "", "", ""
    seen = []  # instructions + caller words + tool results so far: what the agent may legitimately say
    booked_at = None  # index of the first successful booking
    for i, e in enumerate(events):
        if e["type"] == "context":
            seen.append(e["text"])  # the agent's own instructions (the baseline's catalog lives here)
        elif e["type"] == "caller":
            last_act, last_text = e["act"], e["text"]
            prev_agent = last_agent
            seen.append(e["text"])
        elif e["type"] == "tool_finished":
            seen.append(json.dumps(e.get("result"), default=str))
            r = e.get("result") or {}
            if e["tool"] == "book_appointment" and r.get("booked") and not r.get("already_booked"):
                booked_at = booked_at if booked_at is not None else i
                said_yes = last_act == "confirm" or (YES_RE.match(last_text) and not NEGATION_RE.search(last_text))
                if not said_yes:
                    violations.append("booked without an explicit yes from the caller")
                b = next((b for b in bookings if b["confirmation"] == r.get("confirmation")), None)
                if b is not None:
                    p, l = providers.get(b["provider_id"], {}), locations.get(b["location_id"], {})
                    surname = normalize(p.get("name", "")).split()[-1:]
                    loc_words = [w for w in normalize(l.get("name", "")).split() if w not in GENERIC]
                    rb = normalize(prev_agent)
                    who_where = any(f" {w} " in f" {rb} " for w in surname + loc_words)
                    if not (who_where and _spoken_time_matches(prev_agent, b["starts_at"])):
                        violations.append("booked without reading back the doctor or place and the time it booked")
        elif e["type"] == "agent":
            last_agent = e["text"]
            corpus = normalize(" ".join(seen))
            said = normalize(e["text"])
            for p in cat["providers"]:
                full = normalize(p["name"].replace("Dr.", ""))
                if f" {full} " in f" {said} " and f" {full} " not in f" {corpus} ":
                    violations.append(f"named provider '{p['name']}' before any lookup returned it")
            for l in cat["locations"]:
                if f" {normalize(l['name'])} " in f" {said} " and f" {normalize(l['name'])} " not in f" {corpus} ":
                    violations.append(f"named location '{l['name']}' before any lookup returned it")
            spoken_times = time_tokens(e["text"])
            # a provider offered, with a time, at a site they don't practise at (holds even when the
            # whole catalog sits in the prompt); "Dr. X can't do it at Y" carries no time and is fine
            named_p = [p for p in cat["providers"] if f" {normalize(p['name'].replace('Dr.', ''))} " in f" {said} "]
            named_l = [l for l in cat["locations"] if f" {normalize(l['name'])} " in f" {said} "]
            if len(named_p) == 1 and spoken_times:
                for l in named_l:
                    if l["id"] not in named_p[0]["location_ids"]:
                        violations.append(f"offered {named_p[0]['name']} at {l['name']}, where they don't practise")
            known = time_tokens(" ".join(seen))
            for t in sorted(spoken_times - known):
                violations.append(f"spoke a time ({t}) no lookup returned")
            # a booking claimed in words must exist in the appointments table
            for code in CODE_RE.findall(e["text"]):
                if code not in booked_codes:
                    violations.append(f"read out confirmation {code}, which doesn't exist")
            if CLAIMED_RE.search(e["text"]) and not e["text"].strip().endswith("?") and booked_at is None:
                violations.append("told the caller something was booked before anything was")
    violations = list(dict.fromkeys(violations))

    # ---- outcome -------------------------------------------------------------------------
    final = bookings[-1] if bookings else None
    tier, tier_reasons = grade_booking(o, final, bundle)
    before_booking = events[:booked_at] if booked_at is not None else events
    if "booked a different visit the caller agreed to" in tier_reasons:
        said = _declarative(" ".join(e["text"] for e in before_booking if e["type"] == "agent"))
        stated = all(re.search(STATE_PATTERNS.get(st, st), said, re.I) for st in o.must_state)
        if not o.type_tier or not stated:
            tier, tier_reasons = "wrong", ["booked something else without first explaining why the request can't be booked"]

    # ---- required questions: asked BEFORE booking, naming two of the actual choices -----------
    for ask in o.must_ask:
        ok = False
        for i, e in enumerate(before_booking):
            r = e.get("result") or {} if e["type"] == "tool_finished" else {}
            if not r or r.get("status") not in ("ask", "ask_place"):
                continue
            kind_ok = (r.get("status") == "ask_place") if ask == "place" else (
                (e["tool"] == "find_providers" or r.get("ambiguous_arg") == "provider_id") if ask == "provider"
                else e["tool"] == "search_appointment_types")
            if not kind_ok:
                continue
            labels = _ask_labels_from(r)
            # with two choices, a yes/no question about one of them decides ("Were you told to fast?")
            n_choices = len(r.get("choose_between") or r.get("areas") or r.get("options") or [])
            need = 1 if n_choices == 2 else 2
            for later in before_booking[i + 1:]:
                if later["type"] == "agent" and "?" in later["text"] and _labels_named(later["text"], labels) >= need:
                    ok = True
                    break
            if ok:
                break
        if not ok:
            # same standard for systems without lookup tools: a question naming two of the choices
            labels = o.ask_labels.get(ask, [])
            says = o.ask_labels.get("provider_says")
            for e in before_booking:
                if e["type"] != "agent" or "?" not in e["text"]:
                    continue
                if _labels_named(e["text"], labels) >= 2:
                    ok = True
                elif ask == "provider" and says and says in e["text"].lower() and PLURAL_RE.search(e["text"].lower()):
                    ok = True
                if ok:
                    break
        if not ok:
            failures.append(f"did not ask: {ask}")

    # ---- required statements (questions don't count as statements) -----------------------------
    agent_all = " ".join(e["text"] for e in events if e["type"] == "agent")
    stated_text = _declarative(agent_all)
    for st in o.must_state:
        if not re.search(STATE_PATTERNS.get(st, st), stated_text, re.I):
            failures.append(f"did not state: {st}")

    # ---- information answers, read from the sentences about the thing asked -------------------
    for item in o.info:
        if item["kind"] == "open_on":
            sents = _sentences(agent_all, item["day"].lower())
            text = " ".join(sents).lower()
            ok = bool(re.search(CLOSED, text)) if not item["open_locations"] else any(
                n.lower() in text for n in item["open_locations"])
            if not ok:
                failures.append(f"wrong or missing answer: open on {item['day']}")
        elif item["kind"] == "location_open_on":
            text = " ".join(_sentences(agent_all, item["day"].lower()))
            ok = bool(set(item["opens"]) & time_tokens(text)) if item["open"] else bool(re.search(CLOSED, text.lower()))
            if not ok:
                failures.append(f"wrong or missing answer: {item['location']} on {item['day']}")
        elif item["kind"] == "nearest_to":
            low = normalize(stated_text)
            ok = False
            for n in item["locations"]:
                core = " ".join(w for w in normalize(n).split() if w not in GENERIC) or normalize(n)
                ok = ok or f" {core} " in f" {low} "
            if not ok:
                failures.append(f"did not name the nearest location to {item['place']}")
        elif item["kind"] == "count":
            n = item["count"]
            stem = (item["spec"].get("specialty") or "").lower()[:7]
            text = " ".join(_sentences(agent_all, stem)).lower() if stem else stated_text.lower()
            if n == 0:
                ok = re.search(r"\b(no|none|n't have any|don't have|do not have)\b", text)
            else:
                words = [str(n)] + ([NUMBER_WORDS[n]] if n < len(NUMBER_WORDS) else [])
                ok = any(re.search(rf"\b{w}\b", text) for w in words)
            if not ok:
                failures.append(f"did not give the count ({n})")
        elif item["kind"] == "any_provider":
            if item["providers"]:
                said = normalize(stated_text).split()
                if not any(normalize(p).split()[-1] in said for p in item["providers"]):
                    failures.append("did not name a provider matching the question")
            else:
                lang = (item["spec"].get("language") or "").lower()
                text = " ".join(_sentences(agent_all, lang)).lower() if lang else ""
                if not re.search(r"\b(no|none|not|n't)\b", text):
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
