#
# Meaning layer for appointment types (decisions 004, 005, 009).
#
# Three rankers run INSIDE an already-constrained candidate set and are fused with
# reciprocal rank fusion:
#   alias   - curated phrase table, exact and free
#   text    - Postgres full-text over name + specialty + aliases
#   vector  - pgvector cosine over embedded names and aliases
#
# The output is a ranking plus a verdict. A registered cluster or a near-tie means
# "ask" (never argmax a near-tie); a weak best match means "unsure". Admissibility is
# not decided here: the policy relation does that.
#

from dataclasses import dataclass, field

from loguru import logger

from . import embeddings
from .bundle import normalize

RRF_K = 60
GAP_ASK = 0.04        # vector top1 - top2 below this, with no lexical evidence: ask
ABSOLUTE_MIN = 0.35   # best vector similarity below this: unsure, whatever else matched
STRONG = 0.55         # at or above this the vector alone is confident (measured, see decision 013)


@dataclass
class TypeRanking:
    status: str                      # match | ask | unsure | none
    ranked: list                     # [(type_id, fused score, {ranker: rank})]
    cluster: dict = None             # the registered cluster driving an "ask"
    candidates: list = field(default_factory=list)   # the ids the caller must choose among
    similarity: dict = field(default_factory=dict)   # type_id -> best cosine


async def _alias_hits(conn, cv: str, query: str, candidates: list) -> tuple:
    """(type ids, matched phrases): alias phrases occurring in the query as whole words;
    the longest phrase wins ("knee mri" beats "knee")."""
    q = f" {normalize(query)} "
    rows = await conn.fetch(
        "SELECT phrase, appointment_type_id FROM type_aliases WHERE catalog_version = $1 AND appointment_type_id = ANY($2)",
        cv, candidates)
    matched = {r["phrase"] for r in rows if f" {r['phrase']} " in q}
    # "knee mri" beats "knee": drop phrases contained in a longer matched phrase
    keep = {p for p in matched if not any(p != o and f" {p} " in f" {o} " for o in matched)}
    return list(dict.fromkeys(r["appointment_type_id"] for r in rows if r["phrase"] in keep)), keep


async def _name_hits(conn, cv: str, query: str, candidates: list, phrases: set) -> list:
    """A type whose full name the caller actually said is a disambiguator, not a guess,
    unless it is only part of a longer curated phrase ("ultrasound" in "heart ultrasound")."""
    q = f" {normalize(query)} "
    rows = await conn.fetch(
        "SELECT id, name FROM appointment_types WHERE catalog_version = $1 AND id = ANY($2)", cv, candidates)
    hits = []
    for r in rows:
        name = normalize(r["name"])
        if f" {name} " in q and not any(name != p and f" {name} " in f" {p} " for p in phrases):
            hits.append(r["id"])
    return hits


async def _text_ranking(conn, cv: str, query: str, candidates: list) -> list:
    words = [w for w in normalize(query).split() if len(w) > 1]
    if not words:
        return []
    rows = await conn.fetch(
        """SELECT id, ts_rank_cd(search_doc, q) AS r
           FROM appointment_types, to_tsquery('english', $3) q
           WHERE catalog_version = $1 AND id = ANY($2) AND search_doc @@ q
           ORDER BY r DESC, id LIMIT 10""",
        cv, candidates, " | ".join(words))
    return [r["id"] for r in rows]


async def _vector_ranking(conn, cv: str, query: str, candidates: list) -> list:
    vec = (await embeddings.embed(conn, [normalize(query)]))[normalize(query)]
    rows = await conn.fetch(
        """SELECT appointment_type_id AS id, max(1 - (embedding <=> $3)) AS sim
           FROM type_embeddings
           WHERE catalog_version = $1 AND appointment_type_id = ANY($2) AND model = $4
           GROUP BY appointment_type_id ORDER BY sim DESC, id LIMIT 10""",
        cv, candidates, vec, embeddings.MODEL)
    return [(r["id"], float(r["sim"])) for r in rows]


async def rank_types(conn, cv: str, query: str, candidates: list, use_vectors: bool = True) -> TypeRanking:
    if not candidates or not normalize(query):
        return TypeRanking("none", [])
    alias, phrases = await _alias_hits(conn, cv, query, candidates)
    named = await _name_hits(conn, cv, query, candidates, phrases)
    text = await _text_ranking(conn, cv, query, candidates)
    vector = []
    if use_vectors:
        try:
            vector = await _vector_ranking(conn, cv, query, candidates)
        except Exception as e:  # degradation ladder (decision 008): lexical only, and say so
            logger.warning(f"vector ranking unavailable, lexical only: {type(e).__name__}: {e}")
            vector = []
    sims = dict(vector)

    scores: dict = {}
    ranks: dict = {}
    for ranker, ids in (("name", named), ("alias", alias), ("text", text), ("vector", [i for i, _ in vector])):
        for pos, tid in enumerate(ids):
            # name and alias hits are unordered sets: every member ties at rank 1
            rank = 1 if ranker in ("name", "alias") else pos + 1
            scores[tid] = scores.get(tid, 0.0) + 1.0 / (RRF_K + rank)
            ranks.setdefault(tid, {})[ranker] = rank
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    ranked = [(tid, s, ranks[tid]) for tid, s in ranked]
    if not ranked:
        return TypeRanking("none", [], similarity=sims)

    clusters = await conn.fetch(
        "SELECT id, members, question, member_hints, safer_default FROM ambiguity_clusters WHERE catalog_version = $1", cv)

    def cluster_of(ids: set):
        for c in clusters:
            members = set(c["members"]) & set(candidates)
            if len(ids & members) >= 2:
                return c, [m for m in c["members"] if m in members]
        return None, []

    # 1. The caller said a type's exact name: that is their choice.
    if len(named) == 1:
        return TypeRanking("match", ranked, candidates=named, similarity=sims)

    # 2. Curated phrasing pointing at several types: registered ambiguity, ask.
    if len(alias) > 1:
        c, members = cluster_of(set(alias))
        return TypeRanking("ask", ranked, cluster=dict(c) if c else None,
                           candidates=members if c else alias, similarity=sims)
    if len(alias) == 1:
        return TypeRanking("match", ranked, candidates=alias, similarity=sims)

    # 3. No curated phrase: look at the fused ranking.
    top = [tid for tid, _, _ in ranked[:3]]
    best = vector[0][1] if vector else None
    gap = vector[0][1] - vector[1][1] if len(vector) > 1 else 1.0
    if best is not None and best < STRONG:
        # Measured: junk ("hello", "an appointment") and real lay phrasing ("sore throat and
        # ears") both score ~0.40-0.50, so absolute similarity alone can't tell them apart.
        # Lexical support plus a clear gap can; without both, don't pretend to know.
        if not text or gap < GAP_ASK or best < ABSOLUTE_MIN:
            return TypeRanking("unsure", ranked, candidates=top, similarity=sims)
    # a registered cluster fires only when one ranker on its own puts two members on top
    for ids in ([i for i, _ in vector[:2]], text[:2]):
        c, members = cluster_of(set(ids)) if len(ids) == 2 else (None, [])
        if c:
            return TypeRanking("ask", ranked, cluster=dict(c), candidates=members, similarity=sims)
    if best is not None and gap < GAP_ASK and not text:
        near = [tid for tid, s in vector if vector[0][1] - s < GAP_ASK][:3]
        return TypeRanking("ask", ranked, candidates=near, similarity=sims)
    return TypeRanking("match", ranked, candidates=[ranked[0][0]], similarity=sims)
