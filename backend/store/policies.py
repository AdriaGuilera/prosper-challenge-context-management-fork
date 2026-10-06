#
# Reference policy engine: the six booking policies in plain Python, written
# straight from the catalog's prose and independently of the SQL in schema.sql.
#
# Two consumers:
#   - the differential test, which proves the SQL `bookable` relation agrees with
#     this on every raw (provider, location, type) triple and patient state
#   - the eval oracle, which computes each persona's ground truth per catalog
#
# Nothing here touches the database.
#

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class PatientState:
    is_new: bool
    referral_type_ids: frozenset = field(default_factory=frozenset)


def raw_triples(catalog: dict) -> list:
    """Every (provider, location, type) a provider's own lists could pair up."""
    return [
        (p["id"], loc, t)
        for p in catalog["providers"]
        for loc in p["location_ids"]
        for t in p["appointment_type_ids"]
    ]


def triple_problems(catalog: dict, provider_id: str, location_id: str, type_id: str,
                    patient: Optional[PatientState] = None) -> list:
    """Every policy the triple breaks, as machine-readable codes. Empty means bookable."""
    providers = {p["id"]: p for p in catalog["providers"]}
    locations = {l["id"]: l for l in catalog["locations"]}
    types = {t["id"]: t for t in catalog["appointment_types"]}
    p, loc, t = providers.get(provider_id), locations.get(location_id), types.get(type_id)
    if p is None or loc is None or t is None:
        return ["unknown_entity"]
    problems = []
    if location_id not in p["location_ids"]:                         # policy 1
        problems.append("provider_not_at_location")
    if type_id not in p["appointment_type_ids"]:                     # policy 2
        problems.append("provider_does_not_offer_type")
    cap = t.get("required_capability")
    if cap and cap not in loc.get("capabilities", []):              # policy 3
        problems.append("location_lacks_capability")
    if patient is not None:
        if t.get("requires_referral") and type_id not in patient.referral_type_ids:  # policy 4
            problems.append("referral_required")
        if patient.is_new and not t.get("new_patients_allowed"):     # policy 5a
            problems.append("type_closed_to_new_patients")
        if patient.is_new and not p.get("accepting_new_patients"):   # policy 5b
            problems.append("provider_not_accepting_new_patients")
    return problems


def valid_triples(catalog: dict, patient: Optional[PatientState] = None) -> set:
    return {tr for tr in raw_triples(catalog) if not triple_problems(catalog, *tr, patient=patient)}


def multi_location_problem(catalog: dict, provider_id: str, location_id: Optional[str],
                           offered_locations: set) -> Optional[str]:
    """Policy 6, a commit-time state check: a provider at several sites needs the
    location settled in the conversation, i.e. the booked site was the one offered."""
    p = next((p for p in catalog["providers"] if p["id"] == provider_id), None)
    if p is None:
        return "unknown_entity"
    if len(p["location_ids"]) > 1 and (not location_id or location_id not in offered_locations):
        return "location_not_disambiguated"
    return None


def orphan_types(catalog: dict) -> list:
    """Types no provider offers at all."""
    offered = {t for p in catalog["providers"] for t in p["appointment_type_ids"]}
    return [t["id"] for t in catalog["appointment_types"] if t["id"] not in offered]


def unbookable_types(catalog: dict) -> list:
    """Types offered by someone but bookable nowhere (e.g. the capability is missing everywhere)."""
    offered = {t for p in catalog["providers"] for t in p["appointment_type_ids"]}
    ok = {t for _, _, t in valid_triples(catalog)}
    return [t for t in offered if t not in ok]
