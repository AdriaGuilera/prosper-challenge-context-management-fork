# Decision 013: Build the store on Postgres, with pgvector, PostGIS and the gazetteer

**Status:** accepted
**Date:** 2026-10-06
**Supersedes:** decision 010 §2 (ship SQLite) and the "pgvector deferred" timing in 009.
**Revises:** 010 §3 for PostGIS and embeddings. Neither file is edited; this record overrides them.
**Superseded by:** nothing

## Context

Decision 010 shipped SQLite. Its reasoning was that every load-bearing measurement had been taken on
SQLite, and that Postgres had been accepted in 005 but never re-measured. Decision 009 deferred
embeddings until the unmatched-phrase rate became a measured bottleneck.

Two things changed:

1. **The owner's call.** Locations and vectors live in Postgres. Names a caller searches with free
   wording are embedded. Doctor names, street names and specialties are not: specialties are a finite
   set, and locations are fictional, so they resolve through the gazetteer from 007.
2. **Every number 010 relied on has now been re-measured on Postgres.** 010 gave "the evidence
   and the stack disagree" as a reason to stay on SQLite. That reason is gone: the differential test,
   the slot arithmetic and the tool contract all run against the Postgres store (§ Verified below).

## Decision

**One Postgres 17 instance** (`docker-compose.yml`, host port 5433):

- pgvector 0.8 for the meaning layer
- PostGIS 3.6 for proximity
- pg_trgm and fuzzystrmatch for names
- asyncpg for access, so there is no synchronous I/O in the turn path (011)

**What goes where:**

| Thing the caller says | Mechanism | Why |
|---|---|---|
| a visit ("skin check", "my knee") | alias table + full-text search + pgvector, fused with RRF **inside the constrained candidate set** (005) | free wording over an open vocabulary |
| a doctor ("Dr. Nguyen", "Dr. Ngyuen") | trigram + double-metaphone token score (`name_score`), with the distinguishing columns returned | names are identifiers, not meanings; STT mangles spelling, not sound |
| a specialty or capability | JSON-schema `enum` built per catalog | a finite set: the model picks a value, nothing to search |
| a place ("Sunset", "Geary", "the ballpark") | gazetteer: exact or trigram match, then KNN `geom <-> point` | fictional geography (007); no geocoder anywhere |

**Ambiguity is a verdict, not a score.** The type ranker returns `ask` in three cases:

- a curated phrase maps to several types;
- the top two candidates belong to a registered `ambiguity_cluster`;
- with no lexical evidence, the vector top1−top2 gap is under 0.04.

An `ask` carries the cluster's question in the caller's words and its safer default (004). A
best cosine under 0.35, with no lexical hit, returns `unsure`. An exact type name in the caller's
words is a choice and wins outright. Clusters remain the primary runtime trigger, as 009 says.
The vector gap is the fallback for phrasing nobody registered.

**Geometry is planar, SRID 0, units km.** 006 sketched `geography(Point,4326)`. 007 asks for a synthetic
plane where "closest to X is Y" can be checked by hand. Those two conflict, and 007 wins: the
coordinates are fictional, so a spheroid would only add error to a hand-computable oracle.

**Policies:**

- Policies 1–3 are the `bookable` view.
- Policies 4–5 are `bookable_for(cv, is_new, referral_type_ids)`, where the patient state comes from the
  registry, never from the model.
- Policy 6 is a commit-time state check. The booked (provider, location, type, start) must have
  been offered by `check_availability` in this call. That also rules out invented times.
- `book_appointment` re-runs all of it inside a transaction, with a per-provider advisory lock and a
  slot-free check. It also derives the idempotency key from the booking's content, server-side.

**Namespacing (008).** Every row carries `catalog_version`, the sha of the payload. Several catalogs
live side by side, and the eval runs them concurrently. Bookings and run-time patients also carry a
`scope`, so concurrent conversations never see each other's writes.

## Verified on Postgres

- Differential: raw 797, valid **760**, invalid **37** on the given catalog. The SQL relation
  equals the independent Python reference on every triple, for every ingested catalog and six
  patient states. Scrambling every specialty label moves zero triples.
- Slots: the SQL slot query equals the Python reference computed over a calendar regenerated from the
  seed. Schedules never overlap per provider and never fall outside location hours.
- Ingest of the given catalog takes about 0.1 s once embeddings are cached; 285 texts are embedded
  (82 names plus 203 alias rows).

## Consequences

- `docker compose` becomes a dependency of the demo: `make db-up ingest`, then `make run`.
- `type_embeddings` comes back, with model and model version pinned per row. Re-embedding is a
  re-verification event (005).
- An embedding cache, in Postgres plus a local JSONL file, means a reset or a rerun only pays for
  new phrasing.
- The query phrase is embedded at call time: one async API round trip. If it fails, the ranker
  degrades to alias + full-text search (008's ladder).

## What would reverse this

A no-server constraint, which is 005's own reversal clause. Short of that, a measured case where the
vector layer makes the eval worse than alias + full-text search alone. `ToolContext.use_vectors=False`
exists, so that comparison is one flag away.
