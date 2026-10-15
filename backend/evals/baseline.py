#
# The naive baseline the challenge describes: the whole catalog dumped as plain text
# into the agent's context, one node, and only the raw tools it can't do without
# (patient lookup, a raw slot list with NO policy filtering, and booking). Booking
# still goes through the same commit gate, so the store keeps the data safe; what the
# baseline measures is how often the model picks right on its own, and at what cost.
#

from store.bundle import Bundle


def catalog_text(bundle: Bundle) -> str:
    cat = bundle.catalog
    lines = ["CLINIC CATALOG", "", "Booking policies:"]
    lines += [f"- {p}" for p in cat.get("policies", [])]
    lines += ["", "Locations:"]
    for l in cat["locations"]:
        lines.append(f"- {l['id']}: {l['name']}, {l['address']}, {l['city']}. Phone {l['phone']}. Hours: {l['hours']}. "
                     f"Capabilities: {', '.join(l.get('capabilities', [])) or 'none'}.")
    lines += ["", "Providers:"]
    for p in cat["providers"]:
        lines.append(f"- {p['id']}: {p['name']}, {p['title']}, {p['specialty']}. Locations: {', '.join(p['location_ids'])}. "
                     f"Accepting new patients: {'yes' if p['accepting_new_patients'] else 'no'}. "
                     f"Languages: {', '.join(p.get('languages', []))}. Appointment types: {', '.join(p['appointment_type_ids'])}.")
    lines += ["", "Appointment types:"]
    for t in cat["appointment_types"]:
        lines.append(f"- {t['id']}: {t['name']} ({t['specialty']}, {t['duration_min']} min). "
                     f"Requires referral: {'yes' if t['requires_referral'] else 'no'}. "
                     f"New patients allowed: {'yes' if t['new_patients_allowed'] else 'no'}. "
                     f"Required capability: {t.get('required_capability') or 'none'}.")
    return "\n".join(lines)


def baseline_config(bundle: Bundle, model: str) -> dict:
    persona = (
        "You are a warm, efficient scheduling assistant for a multi-specialty clinic, on the phone. Replies are spoken "
        "aloud: one or two short sentences, no lists, no ids. Use ONLY the catalog below to answer questions and choose "
        "the appointment type, provider and location, and honor every booking policy. Ask for the caller's full name "
        "and date of birth and call lookup_patient before booking. Use list_slots for times and only offer times it "
        "returns. Book only after the caller clearly says yes.\n\n" + catalog_text(bundle)
    )
    return {
        "name": "Naive baseline",
        "model": model,
        "persona": persona,
        "initial_node": "schedule",
        "nodes": [
            {
                "name": "schedule",
                "task_messages": [{"role": "developer", "content": "Greet the caller and help them: answer questions or book an appointment."}],
                "tools": ["lookup_patient", "list_slots", "book_appointment"],
                "edges": [{"function": "call_finished", "description": "The caller is done (booked, answered, or nothing more to do).",
                           "target": "goodbye", "properties": {}, "required": []}],
            },
            {
                "name": "goodbye",
                "task_messages": [{"role": "developer", "content": "If something was booked, repeat the confirmation code. Say goodbye."}],
                "end": True,
            },
        ],
    }
