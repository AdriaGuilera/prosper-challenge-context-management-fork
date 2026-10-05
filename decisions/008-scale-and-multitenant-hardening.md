# Decision 008 — Scale hardening: namespaced identity, bounded results, explicit degradation

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** nothing · **Superseded by:** —

## Context

Many clients, hundreds of locations, thousands of providers and appointment types. Two
failure classes: an unbounded result set reaching the model, and identity collisions across
sources.

## Identity is namespaced, always

Entity ids are unique only **within a catalog**. `appt_002` is a Family Medicine annual
physical in this payload and could be anything at all in another source — same for `prov_001`,
`loc_001`. So the real key is `(source_id, catalog_version_id, entity_id)` in tables, caches,
logs, eval cases and persona oracles.

A cache keyed on `appt_002` alone is a cross-tenant leak, and it will present as a
flaky wrong answer rather than as a bug. This applies to the type lexicon, the embeddings
table, the gazetteer and every id carried in a conversation.

## Results are bounded and answer-shaped

- **The limit is fixed server-side.** The tool schema gives the model no `limit` parameter —
  a limit the model sets is not a limit.
- **Never return a bare row array.** Return `{total, shown, truncated, options[]}`.
- **Never truncate silently.** An unflagged truncation makes the model state "there are only
  five" — a correctness bug, not merely a cost one.
- **Return answer-shaped options, not rows**: each resolved and phraseable (provider, location,
  type, slot, spoken phrasing). Hand the model raw rows and it will synthesise, badly.
- **Enforce a per-turn tool-result token budget** by truncation upstream, not by prompt
  instruction.

## Aggregates answer coverage questions

"How many", "do you have any", "what kinds of" → a single aggregate row, collapsing thousands
into one. Never enumerate to answer a counting question. This is where SQL beats every
alternative, and it is free.

## Validation is bounded too

Measured: two policies across 82 types costs **80 ms** in cel-python — thousands of types is
seconds per turn, which no call can absorb. So structural policies run in **SQL**, which is
index-driven and flat, and CEL evaluates only the narrowed top-k. Scale makes the earlier
optimisation mandatory rather than optional.

## Voice cannot paginate

A UI pages. A caller cannot. When the set exceeds k: show the ranked top-k and ask a
**narrowing** question — "soonest, or near a particular place?" Pagination is a builder-UI
feature only.

## Availability joins last

Resolve → rank → top-k → then slots for those k. Joining availability first multiplies the
whole candidate space (measured: 78,784 triples × slots ≈ 1.5M rows per call).

## Degradation ladder, explicit

Narrow k → drop the semantic layer → structural answers only → filler speech while a slow query
runs (the `pre_action` `tts_say` hook). A defined degradation beats a timeout, and one filler
sentence is the difference between "slow" and "broken" on a phone call.

## Per-tenant observability

p95 turn latency, tokens per booking, cache hit rate, query time — reported **per source**, not
averaged. An averaged scalability claim hides the tenant that is failing.

## What would reverse this

A single-tenant deployment: namespacing becomes ceremony, and the bounded-result contract
matters less. Worth keeping anyway — the cost is a composite key, and the alternative is a bug
that only appears when the second client arrives.
