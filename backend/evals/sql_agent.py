#
# Experiment: instead of purpose-built tools, give the agent the database schema and a
# read-only SQL tool, and let it write its own queries. It keeps lookup_patient (the only
# way to the patient registry) and book_appointment, which still re-checks policies 1-5 and
# the slot at commit. Policy 6's "offered by check_availability" is off (there is no
# check_availability here); the time must still be a free slot on the grid.
#
# What the prompt carries is what an engineer would hand a colleague: the tables, what the
# columns mean, the booking policies, and the calendar conventions the tools hide.
#

from datetime import datetime

from store.bundle import Bundle
from store.calendar import HORIZON_DAYS, LEAD

SCHEMA = """\
PostgreSQL 17 with PostGIS. Every table below is already limited to this clinic; just query it by name.

locations(id, name, address, city, phone, hours_text, place, geom)
  geom: point on a flat map in km (SRID 0). Distance: ST_Distance(a.geom, b.geom) or ORDER BY geom <-> point.
location_capabilities(location_id, capability)           -- e.g. 'imaging', 'lab', 'surgery'
location_hours(location_id, weekday, opens, closes)      -- weekday 1=Monday .. 7=Sunday; no row = closed
providers(id, name, title, specialty, accepting_new_patients)
provider_languages(provider_id, language)
provider_locations(provider_id, location_id)             -- where a provider works
appointment_types(id, name, specialty, duration_min, requires_referral, new_patients_allowed, required_capability)
  required_capability: NULL, or a capability the location must have
provider_appointment_types(provider_id, appointment_type_id)  -- what a provider can perform
type_aliases(phrase, appointment_type_id)                -- lowercase caller phrasings that map to a type
ambiguity_clusters(id, members text[], question, member_hints jsonb, safer_default)
  types that are easy to confuse; 'question' is what to ask the caller to tell them apart
schedules(provider_id, location_id, weekday, starts, ends)  -- weekly working blocks (weekday 1=Monday)
appointments(provider_id, location_id, starts_at, ends_at)  -- existing bookings (busy time for that provider)
gazetteer_places(name, kind, aliases text[], geom)       -- neighbourhoods, streets, landmarks the caller may name;
  geom is a MULTIPOINT (a street can span several areas); use ST_Distance(locations.geom, place.geom)

Calendar rules (the booking tool enforces them):
- A slot starts at a schedule block's start and repeats every duration_min of the appointment type, while the whole
  visit fits before the block ends. E.g. block 09:00-12:00, a 45-minute type: 09:00, 09:45, 10:30, 11:15.
- A slot is free when the provider has no appointment (at ANY location) overlapping [start, start + duration).
- Earliest bookable: {lead} minutes from now. Calendar covers the next {horizon} days.
"""


def sql_config(bundle: Bundle, model: str, now: datetime) -> dict:
    policies = "\n".join(f"- {p}" for p in bundle.catalog.get("policies", []))
    lead = int(LEAD.total_seconds() // 60)
    persona = (
        "You are a warm, efficient scheduling assistant for a multi-specialty clinic, on the phone. Replies are spoken "
        "aloud: one or two short sentences, no lists, no ids. You have no catalog in your head: look everything up "
        "with run_sql (locations, providers, appointment types, hours, free times) and only state what a query "
        "returned. Ask for the caller's full name and date of birth and call lookup_patient before booking; it tells "
        "you whether they are new and which referrals they have. Honor every booking policy. Offer only times you "
        "computed as free. Book only after the caller clearly says yes, passing start as YYYY-MM-DDTHH:MM.\n\n"
        f"It is now {now:%A %Y-%m-%d %H:%M}.\n\nBooking policies:\n{policies}\n\nDATABASE\n"
        + SCHEMA.format(lead=lead, horizon=HORIZON_DAYS)
    )
    return {
        "name": "SQL agent (experiment)",
        "model": model,
        "persona": persona,
        "initial_node": "schedule",
        "nodes": [
            {
                "name": "schedule",
                "task_messages": [{"role": "developer", "content": "Greet the caller and help them: answer questions or book an appointment."}],
                "tools": ["lookup_patient", "run_sql", "book_appointment"],
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
