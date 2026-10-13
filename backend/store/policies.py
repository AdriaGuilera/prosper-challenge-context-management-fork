#
# The booking policies: one function per policy, and PolicySet, which runs a set of them.
#
# Every tool decision about "can this be booked" goes through a PolicySet: the commit gate in
# book_appointment, the provider/location pairs check_availability looks at, the filters in the
# lookups, and every refusal reason the caller hears. So "which code enforces policy 4?" has
# one answer: `referral_on_file` below.
#
# A policy checks a Candidate: whatever parts of a booking are known so far (a type alone, a
# provider and a type, or a full provider + location + type). A policy that needs a part the
# candidate doesn't have is skipped, so the same set answers "is this type open to this
# patient?" and "can this exact booking go ahead?".
#
# Independence: the SQL `bookable` / `bookable_for` relation in schema.sql is a second,
# separately written implementation of policies 1-5. tests/test_policies_differential.py
# proves the two agree on every triple of every catalog. Never generate one from the other.
#
# Nothing here touches the database.
#

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class PatientState:
    is_new: bool
    referral_type_ids: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Candidate:
    provider_id: Optional[str] = None
    location_id: Optional[str] = None
    appointment_type_id: Optional[str] = None


@dataclass
class Conversation:
    """What this call has established, for policies about the conversation (policy 6)."""
    offered_locations: dict = field(default_factory=dict)   # provider_id -> {location_id offered to the caller}


class Facts:
    """id -> row maps over a catalog payload, built once per catalog."""

    def __init__(self, catalog: dict):
        self.catalog = catalog
        self.providers = {p["id"]: p for p in catalog["providers"]}
        self.locations = {l["id"]: l for l in catalog["locations"]}
        self.types = {t["id"]: t for t in catalog["appointment_types"]}


# ---- the policies ----------------------------------------------------------------------
# Each takes (facts, candidate, patient, conversation) and returns True when the policy holds.


def provider_works_at_location(f: Facts, c: Candidate, patient, conv) -> bool:
    """1. A provider can only be booked at a location listed in their location_ids."""
    return c.location_id in f.providers[c.provider_id]["location_ids"]


def provider_offers_type(f: Facts, c: Candidate, patient, conv) -> bool:
    """2. A provider can only be booked for an appointment type listed in their appointment_type_ids."""
    return c.appointment_type_id in f.providers[c.provider_id]["appointment_type_ids"]


def location_has_capability(f: Facts, c: Candidate, patient, conv) -> bool:
    """3. A type with a required_capability can only be booked where the location has it."""
    cap = f.types[c.appointment_type_id].get("required_capability")
    return not cap or cap in f.locations[c.location_id].get("capabilities", [])


def referral_on_file(f: Facts, c: Candidate, patient: PatientState, conv) -> bool:
    """4. A type with requires_referral needs a referral on file."""
    return not f.types[c.appointment_type_id].get("requires_referral") or c.appointment_type_id in patient.referral_type_ids


def type_open_to_new_patients(f: Facts, c: Candidate, patient: PatientState, conv) -> bool:
    """5a. A new patient may only book types where new_patients_allowed is true."""
    return not patient.is_new or bool(f.types[c.appointment_type_id].get("new_patients_allowed"))


def provider_accepting_new_patients(f: Facts, c: Candidate, patient: PatientState, conv) -> bool:
    """5b. A new patient may only book providers where accepting_new_patients is true."""
    return not patient.is_new or bool(f.providers[c.provider_id].get("accepting_new_patients"))


def location_settled(f: Facts, c: Candidate, patient, conv: Conversation) -> bool:
    """6. A provider at several locations needs the location settled with the caller before booking:
    the booked location must be one this call offered for that provider."""
    if len(f.providers[c.provider_id]["location_ids"]) <= 1:
        return True
    return c.location_id in conv.offered_locations.get(c.provider_id, set())


# ---- registry ----------------------------------------------------------------------------


def _type(f, c):
    return f.types[c.appointment_type_id]["name"]


@dataclass(frozen=True)
class Policy:
    number: str                         # as in the catalog's policy list: "1".."6", 5 split into 5a/5b
    code: str                           # machine-readable, used by the eval oracle and judge
    check: Callable
    needs: tuple                        # candidate fields the check reads
    on: str                             # "catalog" | "patient" | "conversation": what else it reads
    explain: Callable                   # (facts, candidate) -> the reason, in words a caller can hear

    @property
    def text(self) -> str:
        return self.check.__doc__.split(". ", 1)[1].strip()


POLICIES = [
    Policy("1", "provider_not_at_location", provider_works_at_location, ("provider_id", "location_id"), "catalog",
           lambda f, c: f"{f.providers[c.provider_id]['name']} doesn't work at {f.locations[c.location_id]['name']}."),
    Policy("2", "provider_does_not_offer_type", provider_offers_type, ("provider_id", "appointment_type_id"), "catalog",
           lambda f, c: f"{f.providers[c.provider_id]['name']} doesn't do {_type(f, c)}."),
    Policy("3", "location_lacks_capability", location_has_capability, ("location_id", "appointment_type_id"), "catalog",
           lambda f, c: f"{_type(f, c)} needs {f.types[c.appointment_type_id]['required_capability']}, "
                        f"which {f.locations[c.location_id]['name']} doesn't have."),
    Policy("4", "referral_required", referral_on_file, ("appointment_type_id",), "patient",
           lambda f, c: f"{_type(f, c)} needs a referral on file, and this patient has none for it."),
    Policy("5a", "type_closed_to_new_patients", type_open_to_new_patients, ("appointment_type_id",), "patient",
           lambda f, c: f"{_type(f, c)} isn't open to new patients."),
    Policy("5b", "provider_not_accepting_new_patients", provider_accepting_new_patients, ("provider_id",), "patient",
           lambda f, c: f"{f.providers[c.provider_id]['name']} isn't accepting new patients."),
    Policy("6", "location_not_disambiguated", location_settled, ("provider_id", "location_id"), "conversation",
           lambda f, c: f"{f.providers[c.provider_id]['name']} works at several locations; "
                        "agree the location with the caller first."),
]


@dataclass(frozen=True)
class Violation:
    policy: str
    code: str
    reason: str


class PolicySet:
    """Runs a set of policies over candidates. Build one per catalog; pass `only` to run a subset."""

    def __init__(self, catalog: dict, policies: list = None, only: Optional[set] = None):
        self.facts = Facts(catalog)
        chosen = policies if policies is not None else POLICIES
        self.policies = [p for p in chosen if only is None or p.number in only]
        self._structural = [p for p in self.policies if p.on == "catalog"]

    def __repr__(self) -> str:
        return f"PolicySet({', '.join(p.number for p in self.policies)})"

    def by_number(self, number: str) -> Policy:
        return next(p for p in self.policies if p.number == number)

    # -- checking ---------------------------------------------------------------------------

    def violations(self, c: Candidate, patient: Optional[PatientState] = None,
                   conv: Optional[Conversation] = None) -> list:
        """Every policy the candidate breaks. A policy is skipped when the candidate lacks a part
        it needs, or when it reads patient/conversation state that wasn't passed."""
        f = self.facts
        if any(getattr(c, k) is not None and getattr(c, k) not in table for k, table in
               (("provider_id", f.providers), ("location_id", f.locations), ("appointment_type_id", f.types))):
            return [Violation("-", "unknown_entity", "That provider, location or visit isn't in this clinic's catalog.")]
        out = []
        for p in self.policies:
            if any(getattr(c, k) is None for k in p.needs):
                continue
            if (p.on == "patient" and patient is None) or (p.on == "conversation" and conv is None):
                continue
            if not p.check(f, c, patient, conv):
                out.append(Violation(p.number, p.code, p.explain(f, c)))
        return out

    def allows(self, c: Candidate, patient: Optional[PatientState] = None, conv: Optional[Conversation] = None) -> bool:
        return not self.violations(c, patient, conv)

    # -- bulk: what retrieval filters on --------------------------------------------------------

    def raw_triples(self) -> list:
        """Every (provider, location, type) a provider's own lists could pair up."""
        return [(p["id"], loc, t) for p in self.facts.catalog["providers"]
                for loc in p["location_ids"] for t in p["appointment_type_ids"]]

    def triples(self, patient: Optional[PatientState] = None, provider_id=None, location_id=None, type_id=None) -> set:
        """Bookable (provider, location, type) triples, optionally narrowed. Without a patient,
        only the catalog policies (1-3) apply; with one, 4-5 too. Policy 6 is commit-time only."""
        if patient is None and not (provider_id or location_id or type_id):
            if not hasattr(self, "_all"):
                self._all = self._triples(None)
            return set(self._all)
        return self._triples(patient, provider_id, location_id, type_id)

    def _triples(self, patient, provider_id=None, location_id=None, type_id=None) -> set:
        out = set()
        for p, l, t in self.raw_triples():
            if (provider_id and p != provider_id) or (location_id and l != location_id) or (type_id and t != type_id):
                continue
            if self.allows(Candidate(p, l, t), patient):
                out.add((p, l, t))
        return out

    # -- refusal reasons --------------------------------------------------------------------------

    def explain_none(self, type_id: str, provider_id=None, location_id=None,
                     patient: Optional[PatientState] = None) -> list:
        """Why nothing is bookable for this type (and optional provider/location), in plain words:
        the catalog reason first, else the patient's, each from the policy that blocks it."""
        name = self.facts.types[type_id]["name"]
        structural = self.triples(None, provider_id, location_id, type_id)
        if not structural:
            if not self.triples(None, type_id=type_id):
                return [f"Nobody here offers {name}."]
            if provider_id:
                return [f"That provider can't do {name}" + (" at that location." if location_id else ".")]
            cap = self.facts.types[type_id].get("required_capability")
            return [f"{name} isn't available at that location" + (f" (it needs {cap})." if cap else ".")]
        if patient is None:
            return ["No bookable combination."]
        reasons = []
        for p, l, t in sorted(structural):
            for v in self.violations(Candidate(p, l, t), patient):
                if v.policy == "5b":   # many providers: one sentence, not a list of names
                    reason = "The matching providers aren't accepting new patients."
                else:
                    reason = v.reason
                if reason not in reasons:
                    reasons.append(reason)
        return reasons or ["No bookable combination."]


# ---- module-level API (the eval oracle, judge, and generators use these) ----------------

_SETS: dict = {}


def policy_set(catalog: dict) -> PolicySet:
    """The full set for a catalog object, built once (catalogs are never mutated)."""
    key = id(catalog)
    if key not in _SETS or _SETS[key][0] is not catalog:
        _SETS[key] = (catalog, PolicySet(catalog))
    return _SETS[key][1]


def raw_triples(catalog: dict) -> list:
    return policy_set(catalog).raw_triples()


def triple_problems(catalog: dict, provider_id: str, location_id: str, type_id: str,
                    patient: Optional[PatientState] = None) -> list:
    """Every policy the triple breaks, as machine-readable codes. Empty means bookable."""
    return [v.code for v in policy_set(catalog).violations(Candidate(provider_id, location_id, type_id), patient)]


def valid_triples(catalog: dict, patient: Optional[PatientState] = None) -> set:
    return policy_set(catalog).triples(patient)


def multi_location_problem(catalog: dict, provider_id: str, location_id: Optional[str],
                           offered_locations: set) -> Optional[str]:
    """Policy 6 alone, for one provider."""
    ps = policy_set(catalog)
    if provider_id not in ps.facts.providers:
        return "unknown_entity"
    if not location_id:
        return "location_not_disambiguated" if len(ps.facts.providers[provider_id]["location_ids"]) > 1 else None
    conv = Conversation({provider_id: set(offered_locations)})
    v = [x for x in ps.violations(Candidate(provider_id, location_id), None, conv) if x.policy == "6"]
    return v[0].code if v else None


def orphan_types(catalog: dict) -> list:
    """Types no provider offers at all."""
    offered = {t for p in catalog["providers"] for t in p["appointment_type_ids"]}
    return [t["id"] for t in catalog["appointment_types"] if t["id"] not in offered]


def unbookable_types(catalog: dict) -> list:
    """Types offered by someone but bookable nowhere (e.g. the capability is missing everywhere)."""
    offered = {t for p in catalog["providers"] for t in p["appointment_type_ids"]}
    ok = {t for _, _, t in valid_triples(catalog)}
    return [t for t in offered if t not in ok]
