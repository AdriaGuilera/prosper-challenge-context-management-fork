#
# Meaning layer for appointment types: embeddings shortlist, the Decisions API picks.
#
#   1. Shortlist: the SHORTLIST candidate types whose embedded names are nearest the
#      caller's words (pgvector cosine).
#   2. Pick: one Decisions call asks which shortlisted type fits (or none), with a
#      probability per type.
#
# The output is a ranking plus a verdict. One clear pick means "match"; a pick spread
# over a few types means "ask" between them (never argmax a coin flip), or "unsure".
# Admissibility is not decided here: the policy relation does that.
#

from dataclasses import dataclass, field

from loguru import logger

from . import decisions, embeddings
from .bundle import normalize

SHORTLIST = 10      # measured: the right type was in the top 10 for 66 of 67 lay phrases; 20 picked worse
MAX_CHOICES = 254   # the API takes 255 choices; one is "none"
MATCH_P = 0.6       # the pick's probability at or above this: match
ASK_P = 0.15        # types at or above this are the ones to ask between
NONE = "none of these"


@dataclass
class TypeRanking:
    status: str                      # match | ask | unsure | none
    ranked: list                     # [(type_id, probability)], most likely first
    candidates: list = field(default_factory=list)   # the ids the caller must choose among


async def _shortlist(conn, cv: str, query: str, candidates: list) -> list:
    vec = (await embeddings.embed(conn, [normalize(query)]))[normalize(query)]
    rows = await conn.fetch(
        """SELECT appointment_type_id AS id FROM type_embeddings
           WHERE catalog_version = $1 AND appointment_type_id = ANY($2) AND model = $4
           GROUP BY appointment_type_id ORDER BY min(embedding <=> $3), appointment_type_id LIMIT $5""",
        cv, candidates, vec, embeddings.MODEL, SHORTLIST)
    return [r["id"] for r in rows]


def _labels(types: dict, shortlist: list) -> dict:
    """type_id -> the choice value the API sees: its name, plus the id if the name repeats."""
    names = [types[t]["name"] for t in shortlist]
    return {t: types[t]["name"] if names.count(types[t]["name"]) == 1 else f"{types[t]['name']} [{t}]" for t in shortlist}


def _questions(types: dict, shortlist: list) -> list:
    labels = _labels(types, shortlist)
    return [{
        "type": "choice", "name": "visit",
        "instructions": "Which of the clinic's appointment types would a scheduler book for what the caller said? "
                        "Symptoms, body parts and lay words count: pick the visit that fits them. Pick 'none of "
                        "these' only if the words say nothing about a medical need.",
        "choices": [{"value": labels[t], "description": f"{types[t]['specialty']} visit, "
                                                              f"{types[t]['duration_min']} min"} for t in shortlist]
                   + [{"value": NONE, "description": "No clue to any medical need at all: a greeting, a name, "
                                                     "a yes/no, or just 'an appointment'."}],
    }]


async def rank_types(conn, cv: str, query: str, candidates: list) -> TypeRanking:
    if not candidates or not normalize(query):
        return TypeRanking("none", [])
    types = {r["id"]: dict(r) for r in await conn.fetch(
        "SELECT id, name, specialty, duration_min FROM appointment_types WHERE catalog_version = $1 AND id = ANY($2)",
        cv, candidates)}
    try:
        shortlist = await _shortlist(conn, cv, query, candidates)
    except Exception as e:  # degradation ladder: let the Decisions API choose among every candidate
        logger.warning(f"embedding shortlist unavailable, offering all candidates: {type(e).__name__}: {e}")
        shortlist = sorted(types)[:MAX_CHOICES]
    if not shortlist:
        return TypeRanking("none", [])

    try:
        answers = await decisions.decide(f'A caller to a medical clinic said: "{query}"',
                                         _questions(types, shortlist))
    except Exception as e:  # degradation ladder: the agent asks the caller instead of guessing
        logger.warning(f"Decisions API unavailable, type search unsure: {type(e).__name__}: {e}")
        return TypeRanking("unsure", [(t, 0.0) for t in shortlist], candidates=shortlist[:3])

    visit = answers["visit"]
    if visit["type"] == "refusal":
        return TypeRanking("unsure", [(t, 0.0) for t in shortlist], candidates=shortlist[:3])
    by_name = {label: t for t, label in _labels(types, shortlist).items()}
    probs = {p["value"]: p["probability"] for p in visit["probabilities"]}
    ranked = sorted(((by_name[n], p) for n, p in probs.items() if n in by_name), key=lambda x: (-x[1], x[0]))
    p_top = ranked[0][1] if ranked else 0.0
    # 1. One clear pick.
    if visit["choice"] != NONE and p_top >= MATCH_P:
        return TypeRanking("match", ranked, candidates=[ranked[0][0]])
    # 2. Spread over a few types that carry most of what the types got: ask between them.
    #    This holds even when "none" won: "an MRI" fits every MRI, so no single type wins.
    near = [t for t, p in ranked if p >= ASK_P][:3]
    if len(near) >= 2 and sum(p for _, p in ranked[:len(near)]) >= MATCH_P * sum(p for _, p in ranked):
        return TypeRanking("ask", ranked, candidates=near)
    if visit["choice"] == NONE:
        return TypeRanking("none", ranked)
    return TypeRanking("unsure", ranked, candidates=[t for t, _ in ranked[:3]])
