# Decision 006 — Proximity: PostGIS on stored coordinates, coarse caller area, no geocoder in the call path

**Status:** accepted; the geocoder fallback is superseded by decision 007
**Date:** 2026-10-05
**Supersedes:** nothing · **Superseded by:** 007 (geocoder fallback only)

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

**1. The addresses are fiction, and they contradict the location names.** Every phone number is
in the reserved fictional `(415) 555-xxxx` range, and the eight locations share only **five**
distinct streets drawn from a pool of ten with numbers generated at random from 100–4999:

```
loc_000  Mission Bay Health Center     4874 Main St      (415) 555-9313
loc_001  Mission District Family Clinic 4732 Market St   (415) 555-4657
loc_002  North Beach Specialty Center  481 Mission St    (415) 555-3181
loc_003  North Gate Health Center      944 Bryant St     (415) 555-4078
loc_004  Downtown Health Center        4455 Geary Blvd   (415) 555-6146
loc_005  Midtown Medical Group         2099 Market St    (415) 555-5919
loc_006  Sunset Community Clinic       4293 Geary Blvd   (415) 555-3702
loc_007  Richmond Care Center          4671 Bryant St    (415) 555-6140
```

Some numbers are merely implausible (`4732 Market St` — Market Street runs out long before
that); others are impossible. And the name disagrees with the street geographically: *Sunset
Community Clinic* at a Geary Blvd address, *Richmond Care Center* on Bryant St in SoMa.

Consequence: **for this catalog, geocoding produces coordinates that contradict the names.** A
better answer is available — match on the street name against the catalog's own fields, which
is exact and offline and does not care whether the address is real. Geocoding is for real
tenants. Same code path, different source, recorded per row (`geocode_source`,
`geocode_version`).

**2. The caller can state a place — resolve it with a local gazetteer first.** Stated location
is fine input; what a phone call lacks is *automatic* location (no GPS, area code at best). But
free-form speech needs resolving, and it arrives through STT, so:

- **gazetteer first** — build it from what we already have: the catalog's own street names,
  location names and the neighbourhood list. Exact plus trigram match. Free, offline,
  deterministic, sub-millisecond, and it handles "the Sunset one" and "Geary Blvd" correctly.
- **no geocoder at all** (decision 007). The geography is fictional and complete, so the
  gazetteer is the whole story. If a real tenant ever arrives with real addresses, geocoding is
  a one-off offline batch on some other machine — never in the call path, never resident on this
  box, and never a third-party API, because a residential address spoken during a healthcare
  call is exactly the data that must not leave the building.
- **know the failure modes**: STT garbles street names ("Geary" → "Gary", "Bryant" → "Brian"),
  and street names alone are ambiguous — Market St and Mission St each span the city. Same
  ambiguity class as near-duplicate types: ask, or use the caller's stated area.

For "closest to me" with nothing stated, fall back to a small
`areas(name, aliases[], centroid)` table — eight rows here — rather than asking for a home
address on a recorded call.

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

## Sizing — why no geocoder runs resident here

Nominatim's own figures: minimum **2 GB** to install at all; a **full planet wants 128 GB**
(their words: *do not report out-of-memory problems if you have less than 64 GB*) plus **1 TB
of disk** and ~2.5 days on NVMe to import.

This box is a Hetzner VPS with **7.6 GB RAM (~5 GB available) and 53 GB free disk**, already
running production apps. So:

- full planet — impossible, by two orders of magnitude on both RAM and disk
- country extract — possible but would compete with live apps for memory
- city/metro extract — comfortable, minutes to import, a couple of GB

**But the right answer is none of these.** Eight locations and five distinct streets is a
gazetteer of a few hundred bytes. And if geocoding is ever needed, it belongs **at ingest as a
one-off batch**: RAM becomes a build-time cost, not a runtime one, and only the resulting
coordinates ship. Nothing geocoder-shaped should ever appear in the call path or on this box's
resident memory budget.

Routing (OSRM/Valhalla) is the genuinely heavy dependency if "closest" ever has to mean travel
time. Nominatim's numbers above are verified; OSRM's are not — check before committing to it.

## What would reverse this

Real addresses arriving in a source's payload, which makes geocoding exact rather than
fabricated — and a demo where drive-time ranking is the point.
