# Decision 009 — Alias table and registered ambiguity clusters now; semantic layer deferred

**Status:** accepted · **Supersedes:** the timing in decision 005 (not its mechanism)
**Date:** 2026-10-05

## Context

Decision 005 put Postgres + pgvector in from the start and made embeddings the meaning layer.
Two things changed that:

1. **Our own measurement** of decision 005's claims: recall@1 15/20 but recall@3 19/20;
   augmentation showed **no** benefit; and embeddings did not solve symptom → service
   ("my knee has been hurting" ranks MRI - Knee first at an absolute similarity of 0.424).
2. **A blind four-model panel** (see `reviews/round1-blind-panel.md`) independently converged on
   the opposite order: curated aliases first, embeddings only past a few hundred types, and then
   as *recall* feeding the resolver, never as the final ranker.

## Decision

- **`entity_aliases`** — a curated table mapping caller phrasing to candidate type ids — is the
  primary mapping. It is exact, free, offline, and sub-millisecond.
- **Registered `ambiguity_clusters`** replace the cosine-gap threshold as the runtime trigger for
  asking. A cluster member is never auto-resolved **on ranker score alone** — a caller-supplied
  disambiguator always wins, and the agent must not re-ask after the caller has answered.
  *(The wording "regardless of any score" was too strong; corrected after round-2 review.)*
- **The embedding gap is kept as a discovery mechanism, not a runtime gate.** We measured that it
  separates cleanly (median gap 0.098 for phrases a human hesitates on vs 0.273 for clear ones),
  which makes it a good way to *find* clusters worth registering. It is not needed at runtime.
- **pgvector is deferred**, not rejected. Revisit when unmatched-phrase rate is a measured
  bottleneck — that rate becomes a metric now.
- **If added later**, decision 005's mechanism still stands: rank **inside** an already-constrained
  candidate set, never retrieve from the whole catalog, and never take the argmax of a near-tie.

## Rationale

At 82 types an alias table is ~100% recall on realistic phrasing at zero token and zero latency
cost, and it is auditable and diffable. Embeddings add a model dependency, a version to pin, a
re-embedding event on every catalog change, and — per our own measurement — an ordering that is
unreliable enough that its main value is telling us when to ask. A registered cluster tells us
that more cheaply and more certainly.

The measured result is not wasted: it is the evidence for what happens *if* the catalog grows.

## Consequences

- the ingest path no longer needs an embedding model; `type_embeddings` is dropped from the schema
- the alias table becomes a curated artifact with the same standing as the gazetteer and the policy
  set: versioned, diffable, reviewed
- ambiguity clusters are authored per catalog and registry-checked at ingest
- unmatched-phrase rate is logged, and it is the trigger for revisiting this decision

## What would reverse this

Catalog growth past a few hundred appointment types, or a measured unmatched-phrase rate the alias
table cannot cover. Both are observable, which is the point.
