# Decision 007 — One fictional geography shared by data and tests

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** nothing · **Superseded by:** —

## Context

Proximity needs places with coordinates (decision 006). The catalog's addresses are fiction
already — reserved `555` phone numbers, five distinct streets, numbers generated at random,
and names that contradict their own streets (*Sunset Community Clinic* on Geary Blvd,
*Richmond Care Center* on Bryant St). Geocoding them is meaningless.

Tests therefore cannot use real places: a real-world gazetteer or geocoder makes the suite
non-deterministic, network-dependent, and dependent on geography that changes underneath it.

## Decision

Build **one fictional gazetteer** — a map of places with coordinates — and make it the single
source of geography for **three consumers**:

1. the locations' coordinates in the canonical store
2. the places personas say aloud in tests
3. the caller-side point used for distance ranking

Same file, three readers. If they diverge, tests pass while the demo breaks.

**The relationship is a superset, in one direction only.** Every catalog location must map
*into* the gazetteer, because it needs coordinates. Gazetteer places need **not** correspond to
any location. A persona can say a place where no clinic exists — and that is required, not
merely allowed:

- it makes *"we don't have anything near there"* a testable answer instead of an untested
  assumption
- it stops an agent from passing proximity tests by memorising the eight locations
- it keeps the gazetteer's size independent of the catalog's, so places can grow with the test
  suite rather than with a tenant's data

The gazetteer is therefore the place *vocabulary*: catalog places, persona places, and the
negative cases, in one file.

## What the map must contain

Deliberately designed, not randomly generated, because the interesting cases only exist if the
map is built for them:

- **places served by several clinics** — so proximity is a genuine tiebreak and not decorative
- **a place roughly equidistant between two clinics** — a forced tie: must ask, or break
  deterministically and say which
- **places with no clinic anywhere near** — carried in the gazetteer and referenced by no
  location, so refusal is gradeable
- **deliberate confusables** — one pair that STT is likely to garble ("Geary"/"Gary",
  "Bryant"/"Brian" pattern), so the fuzzy/gazetteer layer has something real to fail on
- **a fixed coordinate plane** — a small synthetic grid, so nearest-neighbour has a
  hand-computable expected answer and distance cases are gradeable without a routing engine

## How the tests use it

The persona/oracle split from the eval design applies directly:

- the **persona speaks the name** ("the one in Northport", "near the river") — never a
  coordinate, never an id
- the **oracle asserts the id** — expected place, expected ranking, expected tie
- the map is versioned with the catalog; persona cases reference the map version, so a change
  to geography is a visible test change, not a silent drift

Because the plane is synthetic, "closest to X is Y" is provable by hand. That is the whole
point: distance cases become deterministic assertions instead of tests against a real city.

## The open fork

**Overlay, or naming authority?**

- *Overlay (leaning)* — the gazetteer supplies coordinates and a place hierarchy; the
  catalog's own location names stay untouched. Their artifact remains immutable, and the
  incoherence between a name and an address is documented rather than edited away.
- *Naming authority* — the fictional map also renames locations, so `Sunset Community Clinic`
  becomes a place in the fictional geography and the data is internally coherent at the cost
  of editing the given payload.

Either works. The overlay keeps the "we did not modify your data" property, which is worth
more in a review than cosmetic coherence.

## Consequences

- no geocoder, no routing engine, no network in any test path
- distance and proximity become first-class gradeable assertions
- the gazetteer gains the same standing as the type lexicon: derived data, versioned, shared
  by runtime and eval
- a fictional map must be documented as fictional wherever a demo touches it, so no one
  mistakes the geography for a real claim
