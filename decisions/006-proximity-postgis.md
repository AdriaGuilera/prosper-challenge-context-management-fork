# Decision 006 — Proximity: PostGIS on stored coordinates, coarse caller area, no geocoder in the call path

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** nothing · **Superseded by:** —

## Context

Location preference is a stated requirement ("honoring preferences — provider, location,
soonest available"), and a caller may name a street, a neighbourhood, or just "closest to me".
The proposal was PostGIS with locations normalised to coordinates, and the same geocoding
service used for both sides.

## Decision

**Yes to PostGIS, coordinates stored at ingest.** Add `geom geography(Point,4326)` to
`locations` with a GiST index, and rank with the KNN operator:

```sql
ORDER BY l.geom <-> :caller_point
```

It sits in the same Postgres instance as `tsvector` and pgvector, so graph, lexical, semantic
and distance ranking all fuse in one query.

**Using the same geocoder on both sides is correct and non-obvious** — mixing services (clinic
via one, caller via another) introduces systematic offset, so the two sides are not in the same
coordinate space. Same service, same version, same country bias.

## Three corrections

**1. These addresses are synthetic, so geocoding them is unreliable.** The catalog generator
produced `f"{random.randint(100, 4999)} {random.choice(STREETS)}"` with `city` hardcoded to
"San Francisco" — `4874 Main St`, `4732 Market St`. They are not real addresses. A geocoder
returns nothing or garbage. For this catalog, **fabricate deterministic coordinates** inside
the SF bounding box; for a real tenant, geocode once at ingest. Same code path, different
source — and the source is recorded (`geocode_source`, `geocode_version`).

**2. A voice call has no location signal, so the caller side is a coarse area, not an address.**
No GPS on a phone call. "Closest to me" resolves to a phone number's area code at best. The
realistic input is a named neighbourhood or ZIP, which needs a small
`areas(name, aliases[], centroid)` table — eight rows here — not a geocoder in the call path.
This also removes the privacy problem: a caller's spoken location never leaves the box.

**3. Straight-line distance is not travel time.** In San Francisco 1.2 km straight-line can be
20 minutes of traffic. Rank by distance, but never *promise* travel time. Real drive-time
ranking needs routing (OSRM/Valhalla, self-hostable — see the `maps` skill) and is out of scope
unless the demo needs it.

## Boundaries

**Distance ranks; it never admits.** Like embeddings, proximity is orthogonal to validity — the
nearest location may lack the required capability or the provider may not practise there. The
validators still decide, and no booking is written on a distance score.

**Street names are ambiguous** — "Market St" spans the city. Same class as near-duplicate
types: ask, or fall back to the caller's stated area.

**Change class:** coordinates are *structural* (rare change, versioned with the catalog) but
**ranking-only**, so re-geocoding never invalidates policy verification. Consistent with
decision 003.

## What would reverse this

Real addresses arriving in a source's payload, which makes geocoding exact rather than
fabricated — and a demo where drive-time ranking is the point.
