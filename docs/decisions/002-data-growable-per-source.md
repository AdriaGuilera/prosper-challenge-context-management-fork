# Decision 002 — Data is growable and per-source; availability is mocked

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** nothing · **Superseded by:** —

## Context

The design's scope depends on whether `backend/data/catalog.json` is a fixed fixture or a
stand-in for data that arrives, grows, and differs per client. The task brief was checked
for a statement either way. It never says *fixed* or *static*.

## What the brief actually specifies

| quote | implication |
|---|---|
| "The starter repo includes a **sample** catalog" | a stand-in, not a fixture |
| "This **simulates the data we could receive** from a multi-specialty clinic" | it models an incoming payload |
| "would your approach hold up with **a large number** of locations, providers, and appointment types?" | growth is a **scored** criterion |
| "you're also free to add a section to the builder UI for **managing these resources**" | resources are mutable, CRUD is expected |
| "Availability/scheduling data isn't included — **mock it however you like**" | one part of the data is ours to invent |
| policies live inside the catalog payload | rules travel *with* the data, per source |

**Not specified anywhere:** multiple hospitals, multiple tenants, schema variance between
sources, or data changing mid-session. Multi-tenancy is an extrapolation from "data we
could receive" — a reasonable one, but it is not in the brief.

## Decision

1. Treat the catalog as **growable** — the design must not assume the 8/50/82 shape.
2. Treat it as **per-source**: rules ship inside the payload, so a policy set belongs to a
   source, not to the application.
3. Treat the **schema as the contract** for now. Arbitrary-schema mapping for
   non-conforming sources is explicitly out of scope, recorded as a scope decision.
4. **Availability is mocked**, in the shape of the `schedules` table the repo dropped
   (`provider_id, location_id, weekday, start, end`), seeded deterministically.

## Rationale

Growth is a scored criterion, so assuming a fixed catalog forfeits marks. Per-source rules
mean a single global validator cannot be correct, which is why policy is data with a
version rather than code. And mocking availability is sanctioned by the brief — the focus
is explicitly "navigating the catalog, not a real calendar".

## Consequences

- every row carries a source/tenant id and a catalog version
- policy sets are versioned and pinned to a catalog version; a data upload invalidates the
  verification evidence and forces re-verification
- a registration/ingest path exists even if only one source is demoed
- the brief's "pragmatic scoping" criterion is served by stating the mapping layer as
  deliberately unbuilt, with the reason

## What would reverse this

The brief saying the catalog is fixed, or a single-source deployment where the payload is
supplied once. In that world the versioning columns are overhead and the IR could be a
static file.
