#
# Catalog tools — the lookups a node can expose to the LLM, by name.
#
# A node lists tool names in its `tools` field; AgentBuilder turns each into a
# FlowsFunctionSchema that answers WITHOUT a transition. Every tool returns a
# small, bounded, answer-shaped result that carries the columns needed to tell
# similar candidates apart — never the whole catalog.
#
# This is the minimal in-memory version over catalog.json. The SQLite store in
# decisions 001/010 replaces the bodies, not the contract.
#

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

CATALOG_PATH = Path(__file__).parent.parent / "data" / "catalog.json"
MAX_RESULTS = 5


def _load() -> dict:
    data = json.loads(CATALOG_PATH.read_text())
    return {
        "locations": {l["id"]: l for l in data["locations"]},
        "providers": {p["id"]: p for p in data["providers"]},
        "types": {t["id"]: t for t in data["appointment_types"]},
    }


CATALOG = _load()


def _words(text: str) -> set:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if w}


def _score(query: str, *fields: str) -> int:
    q = _words(query)
    return sum(len(q & _words(f)) for f in fields if f)


def _bounded(rows: list, key: str) -> dict:
    """Cap a result list and say so — no silent truncation."""
    return {key: rows[:MAX_RESULTS], "total_matches": len(rows), "truncated": len(rows) > MAX_RESULTS}


def _type_row(t: dict) -> dict:
    return {
        "id": t["id"],
        "name": t["name"],
        "specialty": t["specialty"],
        "duration_min": t["duration_min"],
        "requires_referral": t["requires_referral"],
        "new_patients_allowed": t["new_patients_allowed"],
        "required_capability": t.get("required_capability"),
    }


def _provider_row(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "title": p["title"],
        "specialty": p["specialty"],
        "accepting_new_patients": p["accepting_new_patients"],
        "languages": p["languages"],
        "locations": [CATALOG["locations"][l]["name"] for l in p["location_ids"]],
    }


def _location_row(l: dict) -> dict:
    return {
        "id": l["id"],
        "name": l["name"],
        "address": f'{l["address"]}, {l["city"]}',
        "phone": l["phone"],
        "hours": l["hours"],
        "capabilities": l["capabilities"],
    }


def _bookable_locations(provider: dict, appt_type: dict) -> list:
    cap = appt_type.get("required_capability")
    return [
        lid
        for lid in provider["location_ids"]
        if cap is None or cap in CATALOG["locations"][lid]["capabilities"]
    ]


def _booking_problems(provider_id: str, location_id: str, type_id: str, is_new_patient: bool, has_referral: bool) -> list:
    """The commit-time check: every catalog rule, enforced in code."""
    p = CATALOG["providers"].get(provider_id)
    l = CATALOG["locations"].get(location_id)
    t = CATALOG["types"].get(type_id)
    if not (p and l and t):
        return ["Unknown provider, location or appointment type id."]
    problems = []
    if location_id not in p["location_ids"]:
        problems.append(f'{p["name"]} does not practise at {l["name"]}.')
    if type_id not in p["appointment_type_ids"]:
        problems.append(f'{p["name"]} does not offer {t["name"]}.')
    cap = t.get("required_capability")
    if cap and cap not in l["capabilities"]:
        problems.append(f'{l["name"]} has no {cap} for {t["name"]}.')
    if t["requires_referral"] and not has_referral:
        problems.append(f'{t["name"]} needs a referral on file.')
    if is_new_patient and not t["new_patients_allowed"]:
        problems.append(f'{t["name"]} is not open to new patients.')
    if is_new_patient and not p["accepting_new_patients"]:
        problems.append(f'{p["name"]} is not accepting new patients.')
    return problems


# ---- tools ------------------------------------------------------------------


def find_appointment_types(args: dict) -> dict:
    query = args.get("query", "")
    specialty = (args.get("specialty") or "").lower()
    rows = []
    for t in CATALOG["types"].values():
        if specialty and t["specialty"].lower() != specialty:
            continue
        s = _score(query, t["name"], t["specialty"])
        if s or (specialty and not query):
            rows.append((s, t))
    rows.sort(key=lambda r: -r[0])
    return _bounded([_type_row(t) for _, t in rows], "appointment_types")


def find_providers(args: dict) -> dict:
    name = args.get("name") or ""
    specialty = (args.get("specialty") or "").lower()
    type_id = args.get("appointment_type_id")
    location_id = args.get("location_id")
    language = (args.get("language") or "").lower()
    if not (name or specialty or type_id or location_id):
        return {"error": "Give at least one of name, specialty, appointment_type_id or location_id."}
    rows = []
    for p in CATALOG["providers"].values():
        if specialty and p["specialty"].lower() != specialty:
            continue
        if type_id and type_id not in p["appointment_type_ids"]:
            continue
        if location_id and location_id not in p["location_ids"]:
            continue
        if language and language not in [x.lower() for x in p["languages"]]:
            continue
        if name and not _score(name, p["name"]):
            continue
        rows.append((_score(name, p["name"]) if name else 0, p))
    rows.sort(key=lambda r: -r[0])
    return _bounded([_provider_row(p) for _, p in rows], "providers")


def find_locations(args: dict) -> dict:
    query = args.get("query") or ""
    capability = args.get("capability")
    rows = []
    for l in CATALOG["locations"].values():
        if capability and capability not in l["capabilities"]:
            continue
        if query and not _score(query, l["name"], l["city"], l["address"]):
            continue
        rows.append(l)
    return _bounded([_location_row(l) for l in rows], "locations")


def _mock_slots(provider_id: str, location_id: str, type_id: str, count: int = 3) -> list:
    """Deterministic fake availability: same inputs, same slots, weekdays 8-17."""
    seed = int(hashlib.sha256(f"{provider_id}{location_id}{type_id}".encode()).hexdigest(), 16)
    day = date.today() + timedelta(days=1 + seed % 3)
    slots = []
    while len(slots) < count:
        if day.weekday() < 5:
            hour = 8 + (seed >> len(slots) * 4) % 9
            slots.append(datetime(day.year, day.month, day.day, hour))
        day += timedelta(days=1 + (seed >> (len(slots) + 8)) % 2)
    return slots


def check_availability(args: dict) -> dict:
    type_id = args.get("appointment_type_id")
    provider_id = args.get("provider_id")
    location_id = args.get("location_id")
    t = CATALOG["types"].get(type_id)
    if not t:
        return {"error": f"Unknown appointment type '{type_id}'. Look it up first."}
    providers = [CATALOG["providers"][provider_id]] if provider_id in CATALOG["providers"] else [
        p for p in CATALOG["providers"].values() if type_id in p["appointment_type_ids"]
    ]
    options = []
    for p in providers:
        if type_id not in p["appointment_type_ids"]:
            continue
        for lid in _bookable_locations(p, t):
            if location_id and lid != location_id:
                continue
            options.append({
                "provider_id": p["id"],
                "provider": f'{p["name"]}, {p["title"]}',
                "location_id": lid,
                "location": CATALOG["locations"][lid]["name"],
                "slots": _mock_slots(p["id"], lid, type_id),
            })
    options.sort(key=lambda o: o["slots"][0])
    for o in options:
        o["slots"] = [s.strftime("%A %b %d, %-I:%M %p") for s in o["slots"]]
    if not options:
        return {"error": "No bookable provider and location for that combination."}
    return _bounded(options, "options")


def book_appointment(args: dict) -> dict:
    problems = _booking_problems(
        args.get("provider_id", ""),
        args.get("location_id", ""),
        args.get("appointment_type_id", ""),
        bool(args.get("is_new_patient", False)),
        bool(args.get("has_referral", False)),
    )
    if problems:
        return {"booked": False, "problems": problems}
    ref = hashlib.sha1(json.dumps(args, sort_keys=True).encode()).hexdigest()[:6].upper()
    return {"booked": True, "confirmation": f"PR-{ref}", **{k: args[k] for k in ("patient_name", "slot") if k in args}}


# ---- registry ---------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    required: list
    run: Callable[[dict], dict]


_ID = {"type": "string"}

TOOLS = {
    t.name: t
    for t in [
        Tool(
            "find_appointment_types",
            "Search appointment types by what the caller said. Returns at most 5 candidates with the fields that tell them apart.",
            {"query": {"type": "string", "description": "Caller's words, e.g. 'skin check' or 'knee MRI'."},
             "specialty": {"type": "string", "description": "Optional specialty filter."}},
            ["query"],
            find_appointment_types,
        ),
        Tool(
            "find_providers",
            "Find providers by name, specialty, appointment type or location. Names are not unique; results carry specialty, title, languages and locations to disambiguate.",
            {"name": {"type": "string"}, "specialty": {"type": "string"},
             "appointment_type_id": _ID, "location_id": _ID, "language": {"type": "string"}},
            [],
            find_providers,
        ),
        Tool(
            "find_locations",
            "List clinic locations, optionally filtered by name/city or a capability (imaging, lab, physical_therapy, dental, surgery).",
            {"query": {"type": "string"}, "capability": {"type": "string"}},
            [],
            find_locations,
        ),
        Tool(
            "check_availability",
            "Get the soonest open slots for an appointment type, optionally narrowed to a provider and/or location. Only returns provider-location pairs that can actually host the type.",
            {"appointment_type_id": _ID, "provider_id": _ID, "location_id": _ID},
            ["appointment_type_id"],
            check_availability,
        ),
        Tool(
            "book_appointment",
            "Book a confirmed slot. The booking is re-checked against every clinic rule and rejected with reasons if invalid.",
            {"patient_name": {"type": "string"}, "appointment_type_id": _ID, "provider_id": _ID,
             "location_id": _ID, "slot": {"type": "string"},
             "is_new_patient": {"type": "boolean"}, "has_referral": {"type": "boolean"}},
            ["patient_name", "appointment_type_id", "provider_id", "location_id", "slot"],
            book_appointment,
        ),
    ]
}


def describe_tools() -> list:
    """Tool catalog for the builder UI."""
    return [{"name": t.name, "description": t.description, "parameters": list(t.properties)} for t in TOOLS.values()]


def result_size(result: dict) -> Optional[int]:
    for key in ("appointment_types", "providers", "locations", "options"):
        if key in result:
            return len(result[key])
    return None
