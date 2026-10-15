#
# Scenario generators: mine ONE catalog for its own traps and instantiate personas
# that walk into them. Output is ordinary persona dicts (names, never ids), so the
# oracle, the loader, the leak test and the judge treat them exactly like the
# hand-written ones. Deterministic per catalog.
#
#   surname collisions, capability gates,
#   referral gates (incl. expired referrals), new-patient gates, multi-location
#   providers, orphan types, closed days, proximity ties, places far from everything
#

import math
import random
from collections import defaultdict

from store.bundle import Bundle, normalize
from store.hours import DAY_NAMES, HoursError, parse_hours
from store.policies import valid_triples

from .oracle import location_points

FIRST = ["Avery", "Blake", "Casey", "Devon", "Elena", "Farah", "Gabriel", "Hiro", "Imani", "Jonas", "Keira", "Luca",
         "Mina", "Nolan", "Odette", "Pavel", "Quinn", "Rosa", "Silas", "Talia", "Umar", "Vera", "Wen", "Yusuf", "Zara"]
LAST = ["Abbott", "Brennan", "Castillo", "Dubois", "Ekwueme", "Fischer", "Goldberg", "Halvorsen", "Iyer", "Jansen",
        "Kowalski", "Lindqvist", "Mbeki", "Novak", "Oyelaran", "Petrov", "Quintero", "Rasmussen", "Sorensen", "Tanaka"]
BEHAVIOURS = ["cooperative", "vague", "terse-impatient", "rambler", "correction-hunter", "adversarial"]


class _Gen:
    def __init__(self, bundle: Bundle):
        self.b = bundle
        self.rng = random.Random(f"gen:{bundle.name}:{bundle.version}")
        self.cat = bundle.catalog
        self.types = bundle.types
        self.providers = bundle.providers
        self.locations = bundle.locations
        self.structural = valid_triples(self.cat)
        self.points = location_points(bundle)
        self.n = 0
        self.out = []

    # ---- helpers ---------------------------------------------------------------
    def caller(self, existing: bool, referrals=()) -> dict:
        self.n += 1
        first, last = self.rng.choice(FIRST), self.rng.choice(LAST)
        dob = f"{self.rng.randint(1950, 2004)}-{self.rng.randint(1, 12):02d}-{self.rng.randint(1, 28):02d}"
        return {"name": f"{first} {last}", "dob": dob, "record": {"existing": existing, "referrals": list(referrals)}}

    def phrase(self, type_id: str) -> str:
        return self.types[type_id]["name"].lower()

    def place_of(self, loc_id: str) -> str:
        return self.b.places[loc_id]["place"]

    def add(self, kind: str, desc: str, tags: list, caller: dict, expect: dict, behavior: str = None):
        pid = f"gen-{kind}-{sum(1 for p in self.out if p['id'].startswith(f'gen-{kind}-')) + 1}"
        self.out.append({"id": pid, "source": "generated", "description": desc, "tags": ["generated"] + tags,
                         "behaviors": [behavior or self.rng.choice(BEHAVIOURS)], "caller": caller, "expect": expect})

    def plain_types(self, provider_id: str = None) -> list:
        """Types offered somewhere that need no capability and are open to existing patients."""
        ids = {t for p, _, t in self.structural if provider_id is None or p == provider_id}
        return sorted(t for t in ids if not self.types[t].get("required_capability"))

    # ---- generators ------------------------------------------------------------------
    def surname_collisions(self, k=2):
        by_surname = defaultdict(list)
        for p in self.cat["providers"]:
            by_surname[normalize(p["name"]).split()[-1]].append(p["id"])
        pool = sorted(s for s, ids in by_surname.items() if len(ids) >= 2)
        for surname in self.rng.sample(pool, min(k, len(pool))):
            pid = self.rng.choice(sorted(by_surname[surname]))
            types = self.plain_types(pid)
            if not types:
                continue
            tid = self.rng.choice(types)
            t, p = self.types[tid], self.providers[pid]
            locs = sorted({l for pp, l, tt in self.structural if pp == pid and tt == tid})
            loc = self.rng.choice(locs)
            refs = [t["name"]] if t["requires_referral"] else []
            c = self.caller(True, refs)
            c.update({
                "goal": f"Book {self.phrase(tid)} with Dr. {surname.title()}, your {p['specialty']} doctor"
                        + (". You have a referral." if refs else "."),
                "opening": f"Hi, I'd like to book with Dr. {surname.title()}.",
                "answers": {"which doctor": f"{p['name']}, the {p['specialty']} one",
                            "which location": f"the one near {self.place_of(loc)}"},
            })
            self.add("surname", f"'Dr. {surname.title()}' matches {len(by_surname[surname])} providers.",
                     ["collision:surname"], c,
                     {"outcome": "booking", "types": {"ideal": [t["name"]]},
                      "provider": {"name": normalize(p["name"].replace("Dr.", "")), "says": surname, "strict": True},
                      "near": self.place_of(loc)})

    def capability_gates(self, k=2):
        found = []
        for p in self.cat["providers"]:
            for tid in p["appointment_type_ids"]:
                t = self.types.get(tid)
                if not t or not t.get("required_capability"):
                    continue
                ok = [l for l in p["location_ids"] if (p["id"], l, tid) in self.structural]
                bad = [l for l in p["location_ids"] if l not in ok]
                if ok and bad:
                    found.append((p["id"], tid, ok, bad))
        for pid, tid, _, bad in self.rng.sample(found, min(k, len(found))):
            p, t = self.providers[pid], self.types[tid]
            refs = [t["name"]] if t["requires_referral"] else []
            c = self.caller(True, refs)
            near = self.place_of(bad[0])
            c.update({
                "goal": f"Book {self.phrase(tid)} with {p['name']}, ideally at the location near {near}."
                        + (" You have a referral." if refs else "") + " If it can't be done there, another of their locations is fine.",
                "opening": f"Hi, I need {self.phrase(tid)} with {p['name']}, near {near} if possible.",
            })
            self.add("capability", f"{t['name']} needs {t['required_capability']}; {p['name']} also works at sites without it.",
                     ["gate:capability", "multi-location"], c,
                     {"outcome": "booking", "types": {"ideal": [t["name"]]},
                      "provider": {"name": normalize(p["name"].replace("Dr.", "")), "strict": True}, "near": near})

    def referral_gates(self, k=2):
        gated = sorted({t for _, _, t in self.structural if self.types[t]["requires_referral"]})
        for i, tid in enumerate(self.rng.sample(gated, min(k, len(gated)))):
            t = self.types[tid]
            expired = i % 2 == 1
            refs = [{"type": t["name"], "expires": "2025-01-31"}] if expired else []
            c = self.caller(True, refs)
            c.update({
                "goal": f"Book {self.phrase(tid)}."
                        + (" You had a referral for it a while back and assume it's still fine." if expired else
                           " You don't think you need any paperwork."),
                "opening": f"Hi, I'm calling to book an appointment, it's about {self.phrase(tid)}.",
            })
            self.add("referral", f"{t['name']} needs a referral; the caller's is {'expired' if expired else 'missing'}.",
                     ["gate:referral"] + (["expired-referral"] if expired else []), c,
                     {"outcome": "no_booking", "types": {"ideal": [t["name"]]}})

    def new_patient_gates(self, k=2):
        found = []
        for p in self.cat["providers"]:
            if p["accepting_new_patients"]:
                continue
            for tid in p["appointment_type_ids"]:
                t = self.types.get(tid)
                if t and t["new_patients_allowed"] and not t["requires_referral"] and \
                        any(pp == p["id"] and tt == tid for pp, _, tt in self.structural):
                    found.append((p["id"], tid))
        for i, (pid, tid) in enumerate(self.rng.sample(found, min(k, len(found)))):
            p, t = self.providers[pid], self.types[tid]
            strict = i % 2 == 1
            c = self.caller(False)
            c.update({
                "goal": f"You're new to the clinic. Book {self.phrase(tid)} with {p['name']}."
                        + (" You only want that doctor." if strict else " Another doctor is fine if needed."),
                "opening": f"Hi, I'm a new patient and I'd like to see {p['name']} for {self.phrase(tid)}.",
            })
            self.add("newpatient", f"{p['name']} isn't accepting new patients.", ["gate:new-patient"], c,
                     {"outcome": "booking", "types": {"ideal": [t["name"]]},
                      "provider": {"name": normalize(p["name"].replace("Dr.", "")), "strict": strict}})

    def multi_location(self, k=2):
        found = []
        for p in self.cat["providers"]:
            for tid in self.plain_types(p["id"]):
                locs = sorted({l for pp, l, tt in self.structural if pp == p["id"] and tt == tid})
                if len(locs) >= 2 and not self.types[tid]["requires_referral"]:
                    found.append((p["id"], tid, locs))
        for pid, tid, locs in self.rng.sample(found, min(k, len(found))):
            p = self.providers[pid]
            loc = self.rng.choice(locs)
            c = self.caller(True)
            c.update({
                "goal": f"Book {self.phrase(tid)} with {p['name']}. You'd go to whichever of their clinics is closest to {self.place_of(loc)}.",
                "opening": f"Hi, can I book {self.phrase(tid)} with {p['name']}?",
                "answers": {"which location": f"whichever is closest to {self.place_of(loc)}"},
            })
            self.add("multiloc", f"{p['name']} does {self.types[tid]['name']} at {len(locs)} sites (policy 6).",
                     ["multi-location"], c,
                     {"outcome": "booking", "types": {"ideal": [self.types[tid]["name"]]},
                      "provider": {"name": normalize(p["name"].replace("Dr.", "")), "strict": True},
                      "near": self.place_of(loc)})

    def orphans(self, k=2):
        offered = {t for p in self.cat["providers"] for t in p["appointment_type_ids"]}
        orphan = sorted(t["id"] for t in self.cat["appointment_types"] if t["id"] not in offered)
        for tid in self.rng.sample(orphan, min(k, len(orphan))):
            c = self.caller(True)
            c.update({"goal": f"Book {self.phrase(tid)}.", "opening": f"Hi, do you do {self.phrase(tid)}? I'd like to book one."})
            self.add("orphan", f"{self.types[tid]['name']} is in the catalog but nobody offers it.", ["orphan"], c,
                     {"outcome": "no_booking", "types": {"ideal": [self.types[tid]["name"]]}})

    def closed_days(self, k=2):
        found = []
        for l in self.cat["locations"]:
            try:
                week = parse_hours(l["hours"])
            except HoursError:
                continue
            for d in range(7):
                found.append((l["id"], d, d in week))
        closed = [f for f in found if not f[2]]
        opened = [f for f in found if f[2] and f[1] >= 5]  # weekend openings are the interesting "yes"
        picks = self.rng.sample(closed, min(k, len(closed))) + self.rng.sample(opened, min(1, len(opened)))
        for loc_id, d, _ in picks:
            name = self.locations[loc_id]["name"]
            c = self.caller(True)
            c.update({"goal": f"Find out whether {name} is open on {DAY_NAMES[d]}, and if so when. You don't want to book.",
                      "opening": f"Hi, is {name} open on {DAY_NAMES[d]}s?"})
            self.add("hours", f"Is {name} open on {DAY_NAMES[d]}?", ["info", "hours"], c,
                     {"outcome": "information", "info": [{"location_open_on": {"location": name, "day": DAY_NAMES[d]}}]},
                     behavior="cooperative")

    def proximity(self, k=2):
        gz = [p for p in self.b.gazetteer["places"] if len(p["points"]) == 1]
        ties, fars = [], []
        far_km = self.b.gazetteer.get("far_km", 9.0)
        for place in gz:
            d = sorted((math.dist(place["points"][0], xy), loc) for loc, xy in self.points.items())
            if len(d) >= 2 and d[1][0] - d[0][0] <= 0.05 and d[0][0] <= far_km:
                ties.append((place["name"], d[0][1], d[1][1]))
            if d and d[0][0] > far_km:
                fars.append(place["name"])
        for place, a, b in self.rng.sample(ties, min(k, len(ties))):
            common = sorted({t for p, l, t in self.structural if l == a} & {t for p, l, t in self.structural if l == b})
            common = [t for t in common if not self.types[t]["requires_referral"] and not self.types[t].get("required_capability")]
            if not common:
                continue
            tid = self.rng.choice(common)
            c = self.caller(True)
            c.update({"goal": f"Book {self.phrase(tid)} somewhere near {place}, where you live.",
                      "opening": f"Hi, I'd like {self.phrase(tid)}, somewhere close to {place}."})
            self.add("tie", f"{place} is equidistant from two clinics.", ["proximity:tie"], c,
                     {"outcome": "booking", "types": {"ideal": [self.types[tid]["name"]]}, "near": place})
        for place in self.rng.sample(fars, min(1, len(fars))):
            plain = [t for t in self.plain_types() if not self.types[t]["requires_referral"]]
            if not plain:
                continue
            tid = self.rng.choice(plain)
            c = self.caller(True)
            c.update({"goal": f"Book {self.phrase(tid)} near {place}, where you live. If nothing's near, the closest place is fine.",
                      "opening": f"Hi, do you have anything near {place}? I need {self.phrase(tid)}."})
            self.add("far", f"Nothing near {place}.", ["proximity:far"], c,
                     {"outcome": "booking", "types": {"ideal": [self.types[tid]["name"]]}, "near": place})


def generate_personas(bundle: Bundle) -> list:
    g = _Gen(bundle)
    for step in (g.surname_collisions, g.capability_gates, g.referral_gates, g.new_patient_gates,
                 g.multi_location, g.orphans, g.proximity, g.closed_days):
        step()
    # interleave kinds so a --max-generated cut keeps variety
    by_kind = defaultdict(list)
    for p in g.out:
        by_kind[p["id"].rsplit("-", 1)[0]].append(p)
    ordered = []
    while any(by_kind.values()):
        for kind in list(by_kind):
            if by_kind[kind]:
                ordered.append(by_kind[kind].pop(0))
    return ordered
