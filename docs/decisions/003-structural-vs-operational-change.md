# Decision 003 — Two change classes: structural vs operational

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** nothing · **Superseded by:** —

## Context

If the payload is growable and per-source (decision 002), then a source can re-send
*changed* attributes on existing entities — new opening hours being the obvious example.
Approach 002's data model handled that by copying every row into a new `catalog_version`,
which is wrong for two reasons:

1. **Wrong cost.** Opening hours change weekly or daily; copying an entire catalog for an
   hours edit is wasteful and laggy.
2. **Wrong invalidation.** Decision 002 says a data upload invalidates the verification
   evidence and forces re-verification. Hours affect none of the six policies, so daily
   hours changes would force daily re-verification of rules that did not change.

Also verified: `hours` is currently the identical free-text string `"Mon-Fri 8:00-17:00"`
on all eight locations. A free-text blob cannot answer "are you open on Sunday?", cannot
be validated, and cannot be diffed — yet "answering questions about locations" is an
explicit requirement.

## Decision

Split data by change rate, not by entity.

**Structural** — providers, appointment types, capabilities, and the relations between
them. Changes rarely. Versioned by `catalog_version`. Policy-relevant: a change triggers
re-verification.

**Operational** — opening hours, temporary closures, capacity. High churn. Own table with
`valid_from` / `valid_to`, written through a separate operational channel, and **does not**
trigger re-verification.

**Hours are normalised at ingest**, from free text into per-weekday open/close intervals.
An unparseable format is a reported ingest finding, not a silent pass-through. The shape
already existed in this repo — the dropped `schedules` table
(`provider_id, location_id, weekday, start, end`) is exactly the target form.

## The field that sits in both classes

`accepting_new_patients` is high-churn (a provider stops accepting new patients on a
Tuesday) **and** policy-relevant (policy 5). It therefore gets the operational treatment
*and* is the one operational field whose change invalidates verification. It has to be
named explicitly as an exception rather than falling out of the general rule.

## Consequences

- "Are you open on Sunday?" becomes a query instead of an unparseable string comparison
- "the hours in force at the time of booking" is a validity-window lookup, not version
  archaeology
- two write paths: catalog upload (rare, verified) and operational updates (frequent, not)
- the re-verification trigger is a defined field set, not "any upload"
- eval cases must pin both the clock and the hours, or time-dependent cases are ungradeable
- the free-text `hours` field must be coerced at ingest — and remember the binding-type
  trap: string dates and times compare lexicographically, so non-padded and offset formats
  invert rules silently

## What would reverse this

A source that delivers structured availability in the first place, which is what the repo's
dropped `schedules` table did. Or genuinely static hours, in which case the validity
windows are overhead.
