#
# Offline benchmark of the type-search verdict on lay phrasing: no LLM, no simulator.
#
#   python -m evals.search_eval            # given catalog, with and without vectors
#
# Each phrase lists the acceptable type names. A verdict is:
#   right   'match' on an acceptable type, or 'ask' whose choices include one
#   unsure  'unsure' (the agent will ask the caller to rephrase: safe, costs a turn)
#   wrong   'match' on a type that isn't acceptable, or 'ask' among only wrong types
#

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

from store import db  # noqa: E402
from store.search import rank_types  # noqa: E402

PHRASES = [
    ("my knee has been hurting for weeks", ["Orthopedic Consultation", "MRI - Knee", "Physical Therapy Evaluation"]),
    ("I think I need to see a bone doctor", ["Orthopedic Consultation"]),
    ("something for my sore throat and ears", ["ENT Consultation"]),
    ("my heart keeps racing", ["EKG / Electrocardiogram", "Echocardiogram", "Holter Monitor Fitting", "Cardiology Consultation", "Stress Test"]),
    ("a scan of my head", ["MRI - Brain", "CT Scan"]),
    ("I found a lump in my breast", ["Mammogram"]),
    ("my sugar levels are off", ["Diabetes Management", "Endocrinology Consultation"]),
    ("I can't stop worrying, I feel anxious all the time", ["Psychiatric Evaluation", "Therapy Session"]),
    ("I keep forgetting things", ["Memory Evaluation"]),
    ("I need my teeth cleaned", ["Dental Cleaning"]),
    ("a shot before I travel to Kenya", ["Travel Vaccination Consult"]),
    ("I'm pregnant and need a checkup", ["Prenatal Visit"]),
    ("I want to get on birth control", ["Contraception Consultation"]),
    ("I need a tetanus shot", ["Vaccination / Immunization"]),
    ("I'm wheezing and short of breath", ["Pulmonology Consultation", "Spirometry / Lung Function Test"]),
    ("allergies are killing me this spring", ["Allergy Consultation", "Allergy Skin Testing"]),
    ("heartburn every night", ["GI Consultation"]),
    ("my mole changed color", ["Skin Cancer Screening", "Full Body Skin Exam", "Mole Removal"]),
    ("acne flare up again", ["Acne Follow-up", "Dermatology Consultation"]),
    ("I need clearance before my surgery", ["Pre-operative Evaluation"]),
    ("my thyroid levels", ["Thyroid Follow-up", "Endocrinology Consultation"]),
    ("I think I broke my wrist last month and need it checked again", ["Fracture Follow-up"]),
    ("my baby is two weeks old", ["Newborn Visit"]),
    ("my kid has a fever", ["Pediatric Sick Visit"]),
    ("I have the flu, I feel awful", ["Sick Visit", "Urgent Care Visit"]),
    ("a cortisone injection in my shoulder", ["Joint Injection"]),
    ("check my bone density", ["Bone Density Scan (DEXA)"]),
    ("I need blood work for cholesterol", ["Fasting Blood Test", "Blood Draw / Lab Work"]),
    ("hearing has gotten worse", ["Hearing Test / Audiogram"]),
    ("I want botox", ["Cosmetic Consultation"]),
    ("annual exam for my health plan", ["Annual Wellness Visit", "Annual Physical"]),
    ("I need a pap", ["Pap Smear"]),
    ("I have a referral for a colonoscopy", ["Colonoscopy Consultation"]),
    ("someone to talk to about my depression", ["Therapy Session", "Psychiatric Evaluation"]),
    ("my meds need adjusting, I see a psychiatrist", ["Medication Management"]),
    ("migraines every week", ["Neurology Consultation", "Headache Follow-up"]),
    ("a video call with my doctor", ["Telehealth Follow-up"]),
    ("I'm new and want a regular doctor", ["New Patient Visit", "New Patient Consultation"]),
    ("hello", []),
    ("I'd like an appointment please", []),
]


async def run(catalog: str, use_vectors: bool) -> dict:
    pool = await db.pool()
    out = {"right": 0, "unsure": 0, "wrong": 0, "rows": []}
    async with pool.acquire() as conn:
        cv = await conn.fetchval("SELECT catalog_version FROM catalogs WHERE name = $1", catalog)
        names = {r["id"]: r["name"] for r in await conn.fetch(
            "SELECT id, name FROM appointment_types WHERE catalog_version = $1", cv)}
        for phrase, ok in PHRASES:
            r = await rank_types(conn, cv, phrase, list(names), use_vectors)
            chosen = [names[c] for c in r.candidates]
            if not ok:   # content-free: the only right answer is not to pretend
                verdict = "right" if r.status in ("unsure", "none") else "wrong"
            elif r.status in ("unsure", "none"):
                verdict = "unsure"
            else:
                verdict = "right" if set(chosen) & set(ok) else "wrong"
            out[verdict] += 1
            out["rows"].append((phrase, r.status, chosen[:3], verdict))
    return out


async def main(argv) -> None:
    catalog = argv[0] if argv else "given"
    for use_vectors in (True, False):
        res = await run(catalog, use_vectors)
        n = len(PHRASES)
        print(f"\n{catalog} vectors={'on' if use_vectors else 'off'}: right {res['right']}/{n}, "
              f"unsure {res['unsure']}/{n}, wrong {res['wrong']}/{n}")
        for phrase, status, chosen, verdict in res["rows"]:
            if verdict != "right":
                print(f"   {verdict:<6} {status:<7} {phrase!r} -> {chosen}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
