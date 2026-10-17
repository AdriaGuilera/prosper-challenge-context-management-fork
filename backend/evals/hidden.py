#
# The hidden test set: callers written by an LLM from the catalog data alone.
#
# The model sees the persona format and the given catalog, never our personas, code or
# results, so nothing in the agent can have been tuned on these callers. They live in
# eval/personas-hidden/ and run with `--personas hidden`. Don't read their failures to
# tune the agent: once a fix is shaped by one, it is no longer hidden.
#
#   python -m evals.hidden --n 30 --model gpt-5.6-terra
#
# Each persona is checked mechanically (names exist, the oracle builds) and dropped if not.
#

import argparse
import asyncio
import json
import re
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from store.bundle import load_bundle, normalize

from . import llm
from .oracle import compute_oracle, provider_matches
from .personas import EVAL_DIR

load_dotenv(Path(__file__).parent.parent / ".env", override=True)
HIDDEN_DIR = EVAL_DIR / "personas-hidden"
BEHAVIORS = ["cooperative", "vague", "terse-impatient", "rambler", "correction-hunter", "adversarial"]

FORMAT = """Each caller is one JSON object:
{
  "id": "kebab-case-unique-id",
  "description": "one sentence: what this call tests",
  "tags": ["short", "labels"],
  "behaviors": [one or two of: cooperative, vague, terse-impatient, rambler, correction-hunter, adversarial],
  "caller": {
    "name": "First Last", "dob": "YYYY-MM-DD",
    "record": {"existing": true|false, "referrals": ["<exact visit type name>", ...]},
    "goal": "what they want from the call, in the second person, in everyday words",
    "opening": "their first sentence on the phone, in their own words (NOT the catalog's name for the visit)",
    "answers": {"<question they may be asked>": "<their answer>"},
    "preferences": {"<e.g. time | location | doctor>": "<in their words>"}
  },
  "expect": {
    "outcome": "booking" | "no_booking" | "information",
    "types": {"ideal": ["<exact visit type name>"], "acceptable": [...], "suboptimal": [...]},
    "provider": {"name": "<full provider name without Dr.>", "says": "<what the caller calls them, e.g. a surname>",
                 "strict": true|false} | {"specialty": "<exact>", "language": "<exact>", "strict": true|false},
    "near": "<a place name from PLACES>",
    "time": "soonest" | "any",
    "part_of_day": "morning" | "afternoon",
    "must_ask": ["place"] or ["visit:<name>|<name>"],
    "info": [{"count_providers": {"specialty": "<exact>"}} | {"any_provider": {"specialty": "<exact>", "language": "<exact>"}}
             | {"open_on": "<Weekday>"} | {"location_open_on": {"location": "<exact>", "day": "<Weekday>"}}
             | {"nearest_to": "<place from PLACES>"}]
  }
}
Only "outcome" is required in expect. Every other key in expect must come from something the caller states in
"goal", "opening", "preferences" or "answers": a provider only if the caller names them (or asks for a specialty or
language), "near" only if the caller names that place, "part_of_day" only if the caller says morning or afternoon.
The caller can't be graded on what they never say. Callers who want "as soon as possible" use "time": "soonest".
- types: every visit that would meet the caller's need, by how well (ideal > acceptable > suboptimal). If two visits
  both meet it, list both. Omit for "information".
- "record.existing": false makes them a new patient; the clinic's new-patient rules then apply.
- "referrals": only visits the caller really has a referral for. A caller may wrongly believe they have one.
- "outcome" is what a correct clinic should end up doing given the CATALOG and POLICIES: "booking" only if a visit
  in types can actually be booked for this caller; otherwise "no_booking" (the agent must explain why); "information"
  for questions with no booking.
- must_ask: "place" only when the caller names a street that runs through several areas; "visit:A|B" only when the
  caller's words fit several visits AND booking the wrong one would be a different procedure or body part.
- The caller never knows the catalog's ids or exact visit names unless a real patient would."""

INSTRUCTIONS = """You write test callers for a phone scheduling assistant of a medical clinic. Below are the clinic's
CATALOG (visit types, providers, locations), its booking POLICIES, the PLACES on the map, and the caller FORMAT.

Today is {today}. Write {n} distinct callers that a real clinic would get on an ordinary week. Vary them widely: reasons for calling
(common and unusual), symptom descriptions vs named visits, new and existing patients, referrals present, missing or
mistaken, named doctors (full name, surname only, or misremembered), languages, location and time preferences,
requests the clinic can't meet, and pure information questions. About 60% booking, 25% no_booking, 15% information.
Use realistic, varied names and dates of birth (children too). Use EXACT names from the catalog inside "expect" and
"referrals"; the caller's own words ("goal", "opening", "answers") should sound like a person, not the catalog.

Reply ONLY with JSON: {{"personas": [ ... ]}}

FORMAT
{fmt}

POLICIES
{policies}

PLACES (neighbourhoods, towns, landmarks; streets marked)
{places}

CATALOG
{catalog}"""


def catalog_text(bundle) -> str:
    c = bundle.catalog
    T, L = bundle.types, bundle.locations
    lines = ["Visit types (name | specialty | minutes | needs referral | open to new patients | needs equipment):"]
    lines += [f"- {t['name']} | {t['specialty']} | {t['duration_min']} | {t['requires_referral']} | "
              f"{t['new_patients_allowed']} | {t.get('required_capability') or '-'}" for t in c["appointment_types"]]
    lines.append("Locations (name | hours | equipment):")
    lines += [f"- {l['name']} | {l['hours']} | {', '.join(l.get('capabilities', [])) or '-'}" for l in c["locations"]]
    lines.append("Providers (name | specialty | languages | accepting new patients | locations | visits):")
    lines += [f"- {p['name']} | {p['specialty']} | {', '.join(p.get('languages', []))} | {p['accepting_new_patients']} | "
              f"{', '.join(L[x]['name'] for x in p['location_ids'])} | {', '.join(T[x]['name'] for x in p['appointment_type_ids'])}"
              for p in c["providers"]]
    return "\n".join(lines)


def problems(p: dict, bundle) -> list:
    """Why this persona can't be used as written (empty if it can)."""
    out = []
    try:
        exp, caller = p["expect"], p["caller"]
        names = {normalize(t["name"]) for t in bundle.catalog["appointment_types"]}
        for tier, ts in exp.get("types", {}).items():
            out += [f"unknown visit '{t}'" for t in ts if normalize(t) not in names]
        out += [f"unknown referral '{t}'" for t in caller.get("record", {}).get("referrals", []) if normalize(t) not in names]
        if exp.get("provider") and not provider_matches(bundle, exp["provider"]):
            out.append(f"no provider matches {exp['provider']}")
        if exp["outcome"] not in ("booking", "no_booking", "information"):
            out.append(f"bad outcome {exp['outcome']}")
        if exp["outcome"] != "information" and not exp.get("types"):
            out.append("no types")
        # every requirement in expect must be something the caller says (they can't be graded on the rest)
        said = json.dumps({k: caller.get(k) for k in ("goal", "opening", "preferences", "answers")}).lower()
        if "on_or_after" in exp:
            out.append("on_or_after is not part of the format")
        if exp.get("near") and exp["near"].lower() not in said:
            out.append(f"near '{exp['near']}' is never said by the caller")
        if exp.get("part_of_day") and exp["part_of_day"] not in said:
            out.append(f"part_of_day '{exp['part_of_day']}' is never said by the caller")
        pv = exp.get("provider") or {}
        named = pv.get("says") or pv.get("name")
        if named and normalize(named).split()[-1] not in normalize(said):
            out.append(f"provider '{named}' is never named by the caller")
        if pv.get("language") and pv["language"].lower() not in said:
            out.append(f"language '{pv['language']}' is never said by the caller")
        for b in p.get("behaviors", []):
            if b not in BEHAVIORS:
                out.append(f"unknown behaviour {b}")
        date.fromisoformat(caller["dob"])
        compute_oracle(p, bundle, date(2026, 10, 12), datetime(2026, 10, 12, 8))
    except Exception as e:  # anything the oracle can't build from is unusable
        out.append(f"{type(e).__name__}: {e}")
    return out


async def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--model", default="gpt-5.6-terra")
    args = ap.parse_args(argv)
    bundle = load_bundle("given")
    places = "\n".join(f"- {p['name']}" + (" (street)" if p["kind"] == "street" else "")
                       for p in bundle.gazetteer["places"])
    prompt = INSTRUCTIONS.format(today="Monday October 12, 2026", n=args.n, fmt=FORMAT, policies="\n".join(bundle.catalog["policies"]),
                                 places=places, catalog=catalog_text(bundle))
    msg = await llm.chat(args.model, [{"role": "user", "content": prompt}],
                         response_format={"type": "json_object"}, reasoning_effort="medium")
    personas = json.loads(msg.content)["personas"]
    if HIDDEN_DIR.exists():  # a new set replaces the old one whole
        for f in HIDDEN_DIR.glob("*.json"):
            f.unlink()
    HIDDEN_DIR.mkdir(parents=True, exist_ok=True)
    kept, seen = 0, set()
    for p in personas:
        p["id"] = re.sub(r"[^a-z0-9-]", "-", str(p.get("id", "")).lower()).strip("-") or f"hidden-{kept}"
        if p["id"] in seen:
            p["id"] += f"-{kept}"
        p["source"] = "hidden"
        bad = problems(p, bundle)
        if bad:
            print(f"dropped {p['id']}: {'; '.join(bad)}")
            continue
        seen.add(p["id"])
        (HIDDEN_DIR / f"{p['id']}.json").write_text(json.dumps(p, indent=2, ensure_ascii=False) + "\n")
        kept += 1
    print(f"kept {kept} of {len(personas)} -> {HIDDEN_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
