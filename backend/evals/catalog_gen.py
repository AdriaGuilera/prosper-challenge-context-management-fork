#
# Catalog variants for the eval: same schema as the given catalog.json, different
# shape, so the approach is tested on data it was not tuned on.
#
#   python -m evals.catalog_gen            # (re)write every bundle below into backend/catalogs/
#
# Generated (deterministic from a seed):
#   gen-small       3 locations, ~12 providers
#   gen-large       40 locations, 300 providers, ~140 types (82 given + synthetic)
#   gen-collisions  heavy name collisions, incl. identical name+title+specialty twins
#   gen-gates       most types need a referral; half the providers closed to new patients
#   gen-sparse      thin calendar, heavy pre-booking
#   gen-oddhours    weekend, split-shift, evening and one unparseable opening-hours string
# Hand-built adversarial (deliberate, small, every trap placed on purpose):
#   adv-twins           identical twins, confusable clinic names, a capability that exists nowhere
#   adv-referral-maze   specialist care behind referrals, expired referrals, one door for new patients
#   adv-near-dupes      near-identical type names that differ only in duration or wording
#
# Each bundle gets its own gazetteer mapping, lexicon additions and patient registry.
#

import json
import random
from pathlib import Path

from store.bundle import CATALOGS_DIR, GAZETTEER_PATH

GIVEN = json.loads((CATALOGS_DIR.parent / "data" / "catalog.json").read_text())
GAZ = json.loads(GAZETTEER_PATH.read_text())
FIRST = ["Maria", "David", "Wei", "Linda", "Priya", "Carlos", "Fatima", "Kenji", "Olivia", "Daniel", "Grace", "Omar",
         "Hannah", "Lucas", "Sofia", "Samuel", "Nina", "Michael", "Emily", "Andre", "Leila", "Tomas", "Sarah", "Jennifer",
         "Patricia", "Elizabeth", "Ahmed", "Chloe", "Ravi", "Mei", "Jonah", "Ines", "Kofi", "Anya", "Mateo", "Yara"]
LAST = ["Chen", "Garcia", "Nguyen", "Patel", "Smith", "Sato", "Kim", "Martinez", "Hernandez", "Ramirez", "Rodriguez",
        "Singh", "Lee", "Johnson", "Williams", "Okafor", "Novak", "Haddad", "Silva", "Cohen", "Ivanova", "Mensah"]
LANGS = ["English", "Spanish", "Mandarin", "Vietnamese", "Arabic", "Hindi", "Portuguese", "Tagalog", "Korean"]
CLINIC_WORDS = ["Health Center", "Family Clinic", "Medical Group", "Specialty Center", "Community Clinic", "Care Center"]
STREETS = ["Main St", "Market St", "Mission St", "Bryant St", "Geary Blvd", "Oak Ave", "Harbor Rd", "Cedar Ln"]
CLINIC_PLACES = [p["name"] for p in GAZ["places"] if p["kind"] != "street" and p["name"] not in ("Pinecrest", "Harbor Point")]

# Synthetic types for the large catalog: (name, specialty, duration, referral, new ok, capability)
EXTRA_TYPES = [
    ("Sports Medicine Consultation", "Orthopedics", 40, True, True, None),
    ("Sports Injury Follow-up", "Orthopedics", 20, False, False, None),
    ("Shoulder Injection", "Orthopedics", 30, True, False, None),
    ("Sleep Study Consultation", "Pulmonology", 40, True, True, None),
    ("CPAP Follow-up", "Pulmonology", 20, False, False, None),
    ("Wound Care Visit", "Family Medicine", 30, False, True, None),
    ("Stitches Removal", "Family Medicine", 15, False, True, None),
    ("Hepatitis B Vaccine", "Family Medicine", 10, False, True, None),
    ("Shingles Vaccine", "Family Medicine", 10, False, True, None),
    ("Tdap Booster", "Family Medicine", 10, False, True, None),
    ("Weight Management Visit", "Internal Medicine", 30, False, True, None),
    ("Blood Pressure Check", "Internal Medicine", 15, False, True, None),
    ("Smoking Cessation Visit", "Internal Medicine", 30, False, True, None),
    ("Cardiac Rehab Session", "Cardiology", 60, True, False, "physical_therapy"),
    ("Cholesterol Follow-up", "Cardiology", 20, False, False, None),
    ("Eczema Consultation", "Dermatology", 30, True, True, None),
    ("Wart Removal", "Dermatology", 20, False, True, "surgery"),
    ("Psoriasis Follow-up", "Dermatology", 20, False, False, None),
    ("Seizure Follow-up", "Neurology", 30, False, False, None),
    ("Nerve Conduction Study", "Neurology", 60, True, False, None),
    ("Postpartum Visit", "OB/GYN", 30, False, False, None),
    ("IUD Insertion", "OB/GYN", 30, True, True, "surgery"),
    ("Tonsil Evaluation", "ENT", 30, True, True, None),
    ("Ear Wax Removal", "ENT", 20, False, True, None),
    ("Acid Reflux Consultation", "Gastroenterology", 30, True, True, None),
    ("Insulin Pump Training", "Endocrinology", 45, False, False, None),
    ("ADHD Evaluation", "Psychiatry", 90, True, True, None),
    ("Couples Counseling", "Psychiatry", 60, False, True, None),
    ("CT Scan - Abdomen", "Radiology", 30, True, False, "imaging"),
    ("CT Scan - Chest", "Radiology", 30, True, False, "imaging"),
    ("Ultrasound - Pelvic", "Radiology", 30, True, False, "imaging"),
    ("Urine Test", "Lab", 10, False, True, "lab"),
    ("Strep Test", "Lab", 10, False, True, "lab"),
    ("Root Canal Consultation", "Dental", 45, True, False, "dental"),
    ("Tooth Extraction", "Dental", 45, True, False, "dental"),
    ("Kidney Stone Follow-up", "Urology", 20, False, False, None),
    ("Vasectomy Consultation", "Urology", 30, True, True, None),
    ("Pediatric Vaccination", "Pediatrics", 15, False, True, None),
    ("Adolescent Wellness Visit", "Pediatrics", 30, False, True, None),
    ("Lactation Consultation", "Pediatrics", 45, False, True, None),
]
EXTRA_ALIASES = {
    "sports injury": ["Sports Medicine Consultation", "Sports Injury Follow-up"],
    "sleep apnea": ["Sleep Study Consultation", "CPAP Follow-up"], "snoring": ["Sleep Study Consultation"],
    "wound": ["Wound Care Visit"], "stitches out": ["Stitches Removal"], "hep b": ["Hepatitis B Vaccine"],
    "shingles shot": ["Shingles Vaccine"], "tetanus shot": ["Tdap Booster"], "lose weight": ["Weight Management Visit"],
    "blood pressure": ["Blood Pressure Check"], "quit smoking": ["Smoking Cessation Visit"],
    "rash": ["Eczema Consultation", "Dermatology Consultation"], "wart": ["Wart Removal"],
    "seizures": ["Seizure Follow-up"], "after the baby": ["Postpartum Visit"], "iud": ["IUD Insertion"],
    "ear wax": ["Ear Wax Removal"], "heartburn": ["Acid Reflux Consultation"], "adhd": ["ADHD Evaluation"],
    "marriage counseling": ["Couples Counseling"], "ct scan": ["CT Scan", "CT Scan - Abdomen", "CT Scan - Chest"],
    "strep": ["Strep Test"], "pee test": ["Urine Test"], "pull a tooth": ["Tooth Extraction"],
    "root canal": ["Root Canal Consultation"], "kidney stones": ["Kidney Stone Follow-up"],
    "breastfeeding": ["Lactation Consultation"], "teen checkup": ["Adolescent Wellness Visit"],
}
EXTRA_CLUSTERS = [
    {"id": "ct-scan", "members": ["CT Scan", "CT Scan - Abdomen", "CT Scan - Chest"],
     "question": "What part of the body is the scan for: your head, your belly, or your chest?",
     "hints": {"CT Scan": "the head or somewhere else", "CT Scan - Abdomen": "the belly", "CT Scan - Chest": "the chest"},
     "safer_default": None},
    {"id": "sports", "members": ["Sports Medicine Consultation", "Sports Injury Follow-up"],
     "question": "Is this a new injury, or a check-up on one you've already been seen for?",
     "hints": {"Sports Medicine Consultation": "a new injury", "Sports Injury Follow-up": "already been seen for it"},
     "safer_default": "Sports Medicine Consultation"},
]


def _hours(rng, odd: bool) -> str:
    if not odd:
        return "Mon-Fri 8:00-17:00"
    return rng.choice([
        "Mon-Fri 7:30-19:00; Sat 9:00-13:00", "Mon-Fri 8:00-12:00, 13:00-17:00", "Tue-Sat 10am-6pm",
        "Mon,Wed,Fri 8:00-16:00", "Daily 9:00-21:00", "Mon-Thu 12:00-20:00; Fri 8:00-12:00", "Mon-Fri 8:00-17:00",
    ])


def _locations(rng, n: int, odd_hours=False, cap_rate=0.35, names=None) -> list:
    places = list(CLINIC_PLACES)
    rng.shuffle(places)
    caps = ["imaging", "lab", "physical_therapy", "dental", "surgery"]
    out = []
    for i in range(n):
        place = places[i % len(places)]
        name = names[i] if names else f"{place} {CLINIC_WORDS[i % len(CLINIC_WORDS)]}"
        out.append({
            "id": f"loc_{i:03d}", "name": name, "address": f"{rng.randint(100, 4999)} {rng.choice(STREETS)}",
            "city": "San Francisco", "phone": f"(415) 555-{rng.randint(1000, 9999)}", "hours": _hours(rng, odd_hours),
            "capabilities": sorted(c for c in caps if rng.random() < cap_rate),
            "_place": place,
        })
    return out


def _types(extra: bool) -> list:
    types = [dict(t) for t in GIVEN["appointment_types"]]
    if extra:
        for i, (name, spec, dur, ref, new, cap) in enumerate(EXTRA_TYPES):
            t = {"id": f"appt_{82 + i:03d}", "name": name, "specialty": spec, "duration_min": dur,
                 "requires_referral": ref, "new_patients_allowed": new}
            if cap:
                t["required_capability"] = cap
            types.append(t)
    return types


def _providers(rng, n: int, locations: list, types: list, title_mix=("MD", "DO", "NP", "PA"), new_rate=0.8,
               surnames=LAST, firsts=FIRST) -> list:
    by_spec: dict = {}
    for t in types:
        by_spec.setdefault(t["specialty"], []).append(t["id"])
    general = by_spec.get("General", [])
    # specialties with providers, as in the given catalog (types offered as a block per specialty)
    specs = [s for s in by_spec if s not in ("General", "Ophthalmology", "Physical Therapy")]
    out = []
    for i in range(n):
        spec = specs[i % len(specs)] if i < len(specs) else rng.choice(specs)
        block = list(by_spec[spec])
        if spec in ("Family Medicine", "Internal Medicine", "Pediatrics"):
            block += general
        k = rng.choice([1, 1, 2, 2, 3, 4])
        out.append({
            "id": f"prov_{i:03d}", "name": f"Dr. {rng.choice(firsts)} {rng.choice(surnames)}",
            "title": rng.choice(title_mix), "specialty": spec,
            "location_ids": sorted(rng.sample([l["id"] for l in locations], min(k, len(locations)))),
            "accepting_new_patients": rng.random() < new_rate,
            "languages": ["English"] + sorted(rng.sample(LANGS[1:], rng.choice([0, 1, 1, 2]))),
            "appointment_type_ids": sorted(set(block)),
        })
    return out


def _patients(rng, types: list, n=5) -> list:
    refs = [t["name"] for t in types if t["requires_referral"]]
    out = []
    for i in range(n):
        out.append({"id": f"pat_{i + 1:03d}", "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                    "dob": f"{rng.randint(1950, 2005)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                    "referrals": [{"type": r, "expires": "2027-06-30"} for r in rng.sample(refs, min(2, len(refs)))]})
    return out


def _places(locations: list) -> dict:
    """Location -> gazetteer place; clinics sharing a place get small offsets so KNN is defined."""
    pts = {p["name"]: p["points"][0] for p in GAZ["places"]}
    seen: dict = {}
    out = {}
    for l in locations:
        place = l["_place"]
        k = seen.get(place, 0)
        seen[place] = k + 1
        if k == 0:
            out[l["id"]] = place
        else:
            x, y = pts[place]
            out[l["id"]] = {"place": place, "at": [round(x + 0.6 * k, 2), round(y - 0.4 * k, 2)]}
    return out


def derived_aliases(catalog: dict) -> dict:
    """Catalog-specific aliases read off the catalog's own type names: the parts of
    'EKG / Electrocardiogram' and 'Bone Density Scan (DEXA)', and names with their
    punctuation spoken ('X-Ray' -> 'x ray'). Mechanical, so every bundle gets its own."""
    import re
    out: dict = {}
    for t in catalog["appointment_types"]:
        name = t["name"]
        parts = [p.strip() for p in re.split(r"/|\(|\)| - ", name) if p.strip()]
        for part in parts if len(parts) > 1 else []:
            if len(part) >= 3:
                out.setdefault(part.lower(), []).append(name)
        spoken = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        if spoken != name.lower():
            out.setdefault(spoken, []).append(name)
    return out


def write_bundle(name: str, catalog: dict, places: dict, patients: list, calendar: dict,
                 lexicon: dict = None) -> Path:
    d = CATALOGS_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    for l in catalog["locations"]:
        l.pop("_place", None)
    catalog = {"policies": GIVEN["policies"], **{k: v for k, v in catalog.items() if k != "policies"}}
    (d / "catalog.json").write_text(json.dumps(catalog, indent=1) + "\n")
    (d / "places.json").write_text(json.dumps(places, indent=1) + "\n")
    (d / "patients.json").write_text(json.dumps(patients, indent=1) + "\n")
    lexicon = dict(lexicon or {"aliases": {}, "clusters": []})
    lexicon["aliases"] = {**derived_aliases(catalog), **lexicon.get("aliases", {})}
    (d / "lexicon.json").write_text(json.dumps(lexicon, indent=1) + "\n")
    lex_files = ["../lexicon.base.json", "lexicon.json"]
    (d / "bundle.json").write_text(json.dumps({
        "name": name, "catalog": "catalog.json", "lexicon": lex_files, "places": "places.json",
        "patients": "patients.json", "calendar": calendar}, indent=1) + "\n")
    return d


def _generated(name: str, seed: int, n_loc: int, n_prov: int, extra_types=False, odd_hours=False,
               new_rate=0.8, referral_rate=None, surnames=LAST, firsts=FIRST, calendar=None, cap_rate=0.35) -> Path:
    rng = random.Random(seed)
    locations = _locations(rng, n_loc, odd_hours, cap_rate)
    types = _types(extra_types)
    if referral_rate is not None:
        for t in types:
            if t["specialty"] not in ("General", "Family Medicine", "Pediatrics", "Lab"):
                t["requires_referral"] = rng.random() < referral_rate
    providers = _providers(rng, n_prov, locations, types, new_rate=new_rate, surnames=surnames, firsts=firsts)
    if n_prov < 30:  # small catalogs only carry the types someone offers, plus two orphans
        offered = {t for p in providers for t in p["appointment_type_ids"]}
        orphans = [t for t in types if t["id"] not in offered][:2]
        types = [t for t in types if t["id"] in offered] + orphans
    if odd_hours:
        locations[-1]["hours"] = "By appointment only"   # unparseable on purpose: a lint finding
    cat = {"locations": locations, "providers": providers, "appointment_types": types}
    lex = {"aliases": EXTRA_ALIASES, "clusters": EXTRA_CLUSTERS} if extra_types else None
    return write_bundle(name, cat, _places(locations), _patients(rng, types),
                        calendar or {"seed": seed, "anchor": "2026-10-12", "density": "normal", "prebook_rate": 0.3}, lex)


# ---- hand-built adversarial catalogs -------------------------------------------------------


def _type(tid, name, spec, dur, ref=False, new=True, cap=None):
    t = {"id": tid, "name": name, "specialty": spec, "duration_min": dur, "requires_referral": ref,
         "new_patients_allowed": new}
    if cap:
        t["required_capability"] = cap
    return t


def _loc(lid, name, place, hours="Mon-Fri 8:00-17:00", caps=(), address="100 Main St"):
    return {"id": lid, "name": name, "address": address, "city": "San Francisco", "phone": "(415) 555-0100",
            "hours": hours, "capabilities": list(caps), "_place": place}


def _prov(pid, name, title, spec, locs, new, langs, types):
    return {"id": pid, "name": name, "title": title, "specialty": spec, "location_ids": locs,
            "accepting_new_patients": new, "languages": langs, "appointment_type_ids": types}


def adv_twins() -> Path:
    locations = [
        _loc("loc_000", "Gary Park Clinic", "Gary Park", caps=["lab"], address="12 Park Way"),
        _loc("loc_001", "Geary Health Center", "Downtown", caps=["imaging", "lab"], address="900 Geary Blvd"),
        _loc("loc_002", "Northport Imaging", "Northport", caps=["imaging"], address="5 Harbor Rd",
             hours="Mon-Fri 7:00-19:00; Sat 8:00-12:00"),
        _loc("loc_003", "Sunset Skin & Family", "Sunset", caps=["surgery"], address="77 Geary Blvd"),
    ]
    types = [
        _type("appt_000", "Skin Cancer Screening", "Dermatology", 30),
        _type("appt_001", "Full Body Skin Exam", "Dermatology", 30),
        _type("appt_002", "Mole Removal", "Dermatology", 30, ref=True, new=False, cap="surgery"),
        _type("appt_003", "Echocardiogram", "Cardiology", 45, ref=True, new=False, cap="imaging"),
        _type("appt_004", "Cardiology Consultation", "Cardiology", 40, ref=True),
        _type("appt_005", "Annual Physical", "Family Medicine", 30),
        _type("appt_006", "Flu Shot", "Family Medicine", 10),
        _type("appt_007", "Dental Cleaning", "Dental", 45, cap="dental"),   # offered, but no site has dental
        _type("appt_008", "MRI - Knee", "Radiology", 45, ref=True, new=False, cap="imaging"),
    ]
    providers = [
        _prov("prov_000", "Dr. Alex Morgan", "MD", "Dermatology", ["loc_000", "loc_003"], True, ["English", "Spanish"], ["appt_000", "appt_001", "appt_002"]),
        _prov("prov_001", "Dr. Alex Morgan", "MD", "Dermatology", ["loc_001"], True, ["English", "Mandarin"], ["appt_000", "appt_001", "appt_002"]),
        _prov("prov_002", "Dr. Sam Chen", "MD", "Cardiology", ["loc_000", "loc_001", "loc_002"], False, ["English"], ["appt_003", "appt_004"]),
        _prov("prov_003", "Dr. Sam Chen", "NP", "Family Medicine", ["loc_000", "loc_003"], True, ["English"], ["appt_005", "appt_006"]),
        _prov("prov_004", "Dr. Ruth Okafor", "DDS", "Dental", ["loc_000", "loc_003"], True, ["English"], ["appt_007"]),
        _prov("prov_005", "Dr. Ken Ivanova", "MD", "Radiology", ["loc_002"], True, ["English"], ["appt_008", "appt_003"]),
    ]
    patients = [{"id": "pat_001", "name": "Alan Reyes", "dob": "1980-04-02", "referrals": [{"type": "Echocardiogram", "expires": "2027-06-30"}]}]
    cat = {"locations": locations, "providers": providers, "appointment_types": types}
    return write_bundle("adv-twins", cat, _places(locations), patients,
                        {"seed": 11, "anchor": "2026-10-12", "density": "normal", "prebook_rate": 0.3})


def adv_referral_maze() -> Path:
    locations = [_loc(f"loc_00{i}", f"{p} Medical Group", p, caps=c) for i, (p, c) in enumerate(
        [("Midtown", ["imaging", "lab"]), ("Richmond", ["lab"]), ("Riverside", ["imaging", "surgery"]), ("Old Mill", [])])]
    types = [
        _type("appt_000", "Cardiology Consultation", "Cardiology", 40, ref=True),
        _type("appt_001", "Echocardiogram", "Cardiology", 45, ref=True, new=False, cap="imaging"),
        _type("appt_002", "Orthopedic Consultation", "Orthopedics", 40, ref=True),
        _type("appt_003", "MRI - Knee", "Radiology", 45, ref=True, new=False, cap="imaging"),
        _type("appt_004", "Dermatology Consultation", "Dermatology", 30, ref=True),
        _type("appt_005", "Skin Cancer Screening", "Dermatology", 30, ref=True),
        _type("appt_006", "Annual Physical", "Family Medicine", 30),
        _type("appt_007", "Sick Visit", "Family Medicine", 20, new=False),
        _type("appt_008", "Flu Shot", "Family Medicine", 10),
        _type("appt_009", "Neurology Consultation", "Neurology", 45, ref=True, new=False),
    ]
    providers = [
        _prov("prov_000", "Dr. Linda Park", "MD", "Cardiology", ["loc_000", "loc_002"], False, ["English"], ["appt_000", "appt_001"]),
        _prov("prov_001", "Dr. Omar Silva", "MD", "Cardiology", ["loc_001"], True, ["English", "Arabic"], ["appt_000", "appt_001"]),
        _prov("prov_002", "Dr. Grace Novak", "DO", "Orthopedics", ["loc_002", "loc_003"], False, ["English"], ["appt_002"]),
        _prov("prov_003", "Dr. Ines Haddad", "MD", "Radiology", ["loc_000", "loc_002"], True, ["English"], ["appt_003"]),
        _prov("prov_004", "Dr. Mei Cohen", "MD", "Dermatology", ["loc_001", "loc_003"], False, ["English"], ["appt_004", "appt_005"]),
        _prov("prov_005", "Dr. Kofi Mensah", "NP", "Family Medicine", ["loc_000", "loc_001", "loc_003"], True, ["English", "Spanish"], ["appt_006", "appt_007", "appt_008"]),
        _prov("prov_006", "Dr. Anya Patel", "MD", "Family Medicine", ["loc_002"], False, ["English", "Hindi"], ["appt_006", "appt_007", "appt_008"]),
        _prov("prov_007", "Dr. Jonah Lee", "MD", "Neurology", ["loc_001"], True, ["English"], ["appt_009"]),
    ]
    patients = [
        {"id": "pat_001", "name": "Alan Reyes", "dob": "1980-04-02", "referrals": [{"type": "Echocardiogram", "expires": "2026-03-01"}]},
        {"id": "pat_002", "name": "Sam Okafor", "dob": "1992-01-20", "referrals": [{"type": "MRI - Knee", "expires": "2027-03-01"}]},
    ]
    cat = {"locations": locations, "providers": providers, "appointment_types": types}
    return write_bundle("adv-referral-maze", cat, _places(locations), patients,
                        {"seed": 12, "anchor": "2026-10-12", "density": "normal", "prebook_rate": 0.35})


def adv_near_dupes() -> Path:
    locations = [_loc(f"loc_00{i}", f"{p} Clinic", p, caps=c) for i, (p, c) in enumerate(
        [("Downtown", ["lab"]), ("Mission Bay", ["imaging"]), ("North Gate", [])])]
    types = [
        _type("appt_000", "Consultation", "General", 30),
        _type("appt_001", "Initial Consultation", "General", 45),
        _type("appt_002", "Consult - Extended", "General", 60),
        _type("appt_003", "Follow-up Visit", "General", 20, new=False),
        _type("appt_004", "Follow Up Visit (Short)", "General", 10, new=False),
        _type("appt_005", "Annual Physical", "Family Medicine", 30),
        _type("appt_006", "Annual Wellness Visit", "Internal Medicine", 30),
        _type("appt_007", "Annual Exam", "Family Medicine", 40),
        _type("appt_008", "Blood Draw / Lab Work", "Lab", 15, cap="lab"),
        _type("appt_009", "Fasting Blood Test", "Lab", 15, cap="lab"),
        _type("appt_010", "Blood Work - Routine", "Lab", 15, cap="lab"),
        _type("appt_011", "Ultrasound", "Radiology", 30, ref=True, new=False, cap="imaging"),
        _type("appt_012", "Ultrasound - Abdominal", "Radiology", 45, ref=True, new=False, cap="imaging"),
    ]
    providers = [
        _prov("prov_000", "Dr. Ravi Singh", "MD", "Family Medicine", ["loc_000", "loc_002"], True, ["English", "Hindi"],
              ["appt_000", "appt_001", "appt_002", "appt_003", "appt_004", "appt_005", "appt_007"]),
        _prov("prov_001", "Dr. Chloe Williams", "MD", "Internal Medicine", ["loc_000", "loc_001"], True, ["English"],
              ["appt_000", "appt_001", "appt_003", "appt_004", "appt_006"]),
        _prov("prov_002", "Dr. Mateo Silva", "PA", "Lab", ["loc_000"], True, ["English", "Spanish"], ["appt_008", "appt_009", "appt_010"]),
        _prov("prov_003", "Dr. Yara Haddad", "MD", "Radiology", ["loc_001"], True, ["English", "Arabic"], ["appt_011", "appt_012"]),
    ]
    lexicon = {"aliases": {"consultation": ["Consultation", "Initial Consultation", "Consult - Extended"],
                           "first appointment": ["Initial Consultation", "Consultation"],
                           "annual exam": ["Annual Exam", "Annual Physical", "Annual Wellness Visit"],
                           "routine blood work": ["Blood Work - Routine", "Blood Draw / Lab Work"],
                           "belly ultrasound": ["Ultrasound - Abdominal"]},
               "clusters": [{"id": "consult", "members": ["Consultation", "Initial Consultation", "Consult - Extended"],
                             "question": "Is this a quick question for the doctor, a first full visit, or something you'd like a long session for?",
                             "hints": {"Consultation": "a quick question", "Initial Consultation": "a first full visit",
                                       "Consult - Extended": "a long session"}, "safer_default": "Initial Consultation"},
                            {"id": "annual3", "members": ["Annual Physical", "Annual Wellness Visit", "Annual Exam"],
                             "question": "Would you like a hands-on physical, a check-in about your health plan, or the longer full annual exam?",
                             "hints": {"Annual Physical": "a hands-on physical", "Annual Wellness Visit": "a health-plan check-in",
                                       "Annual Exam": "the longer full exam"}, "safer_default": "Annual Exam"},
                            {"id": "blood3", "members": ["Blood Draw / Lab Work", "Fasting Blood Test", "Blood Work - Routine"],
                             "question": "Were you told to fast before it, and is it your routine yearly blood work?",
                             "hints": {"Fasting Blood Test": "told to fast", "Blood Work - Routine": "routine yearly blood work",
                                       "Blood Draw / Lab Work": "a specific test the doctor ordered"}, "safer_default": "Blood Draw / Lab Work"}]}
    patients = [{"id": "pat_001", "name": "Jordan Bell", "dob": "2001-07-08", "referrals": []}]
    cat = {"locations": locations, "providers": providers, "appointment_types": types}
    return write_bundle("adv-near-dupes", cat, _places(locations), patients,
                        {"seed": 13, "anchor": "2026-10-12", "density": "normal", "prebook_rate": 0.3}, lexicon)


def main() -> None:
    written = [
        _generated("gen-small", 101, 3, 12),
        _generated("gen-large", 102, 40, 300, extra_types=True, odd_hours=True),
        _generated("gen-collisions", 103, 8, 50, surnames=["Chen", "Garcia", "Nguyen", "Patel"],
                   firsts=["Maria", "David", "Wei", "Linda"]),
        _generated("gen-gates", 104, 8, 50, new_rate=0.45, referral_rate=0.85),
        _generated("gen-sparse", 105, 6, 20, calendar={"seed": 105, "anchor": "2026-10-12", "density": "sparse", "prebook_rate": 0.6}),
        _generated("gen-oddhours", 106, 8, 40, odd_hours=True),
        adv_twins(), adv_referral_maze(), adv_near_dupes(),
    ]
    for d in written:
        print(d)


if __name__ == "__main__":
    main()
