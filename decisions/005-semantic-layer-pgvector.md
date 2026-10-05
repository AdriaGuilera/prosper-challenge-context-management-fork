# Decision 005 — Semantic layer: Postgres + pgvector, ranking inside a constrained set

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** the SQLite lean in approach 001 · **Superseded by:** —

## Context

A caller says "skin check". Mapping that to catalog vocabulary currently needs either an
authored lexicon (exact, but only covers phrases someone wrote) or an LLM word-expansion
round trip (300 ms–2 s, nondeterministic, in the voice path). The latter is the latency
problem: we do not want the model generating search variants on every utterance.

## Decision

**Store:** Postgres + pgvector. This flips the earlier SQLite lean, and the reason is not
vector scale — it is that Postgres gives lexical search, joins and vectors in one ACID store
with a real planner, plus row-level security for tenancy.

**Embeddings rank inside an already-constrained candidate set — never retrieve from the
whole catalog.** The query is graph/constraint first, vector second:

```sql
SELECT t.id, t.name, e.embedding <=> :q AS distance
FROM provider_appointment_types pat
JOIN appointment_types t ON t.id = pat.appointment_type_id
JOIN type_embeddings  e ON e.type_id = t.id
WHERE pat.provider_id = :provider            -- graph decides eligibility
  AND /* capability + patient gates */        -- validators decide admissibility
ORDER BY e.embedding <=> :q
LIMIT 5;
```

This ordering matters because "top-k then filter" silently loses recall — the surviving rows
may not be in the top-k at all. pgvector 0.8+ adds **iterative index scans** for exactly
this (`SET hnsw.iterative_scan = relaxed_order`, capped by `hnsw.max_scan_tuples`), so the
index keeps walking until enough rows survive the `WHERE` clause.

**Three retrieval modes, one query, fused:**

- graph/join — exact, index-driven (measured 0.017 ms anchored)
- `tsvector` — lexical and fuzzy, replacing the FTS5 role (handles "Dr. Ngyuen")
- pgvector — semantic, for phrasing the lexicon has never seen

Fuse with reciprocal rank fusion. An **exact alias table stays** for high-frequency phrases:
it is free, instant, and it protects the demo from an embedding-model outage.

**Embed an augmented string at ingest** — name plus specialty plus aliases — not the bare
label. Two-to-four token labels embed poorly, and clinical shorthand is underrepresented in
general embedding models.

**A near-tie means ask.** The distance gap between the top two is the ambiguity signal that
drives decision 004: "skin check" lands near both Skin Cancer Screening and Full Body Skin
Exam, because near-identical text produces near-identical vectors. The semantic layer
*discovers* the ambiguity cheaply; it does not remove it. Never take the argmax of a near-tie.

**Pin `embedding_model` alongside the policy set.** Changing the model changes decisions, so
re-embedding is a re-verification event like a catalog change. Cosine thresholds are
versioned policy, not constants buried in code.

## What this does not do

Admissibility. Similarity and validity are orthogonal — the nearest type may need a referral
the patient lacks, or a capability the location does not have. Vectors order candidates; the
validators still decide, and no booking is ever written on a similarity score.

## Consequences

- Postgres replaces SQLite; sqlite-vec exists but at GA is brute-force only, with ANN still
  in alpha — and it gives no hybrid or tenancy story
- `type_embeddings(type_id, model, version, embedding)` as its own table, so re-embedding
  never rewrites the catalog
- the lexicon shrinks from "cover every phrase" to "cover the frequent ones"
- eval gains a metric: recall of the semantic layer against unmapped phrasing

## What would reverse this

A local-only or no-server constraint. At 82 — or even 2,000 — vectors, brute-force
sqlite-vec is numerically adequate; the decision rests on hybrid search, planner and
tenancy, not on vector scale.
