# Decision 004 — Ambiguous near-duplicates are resolved by asking the caller

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** nothing · **Superseded by:** —

## Context

Some appointment types are identical in every stored column — Skin Cancer Screening vs
Full Body Skin Exam, School Physical vs Sports Physical differ on nothing at all, and
Annual Physical vs Annual Wellness Visit differ only on `specialty`, which is a display
label rather than something a patient can answer about. The provider's type list does not
help either, because providers receive their specialty's whole block, so whoever offers one
offers the other.

There is therefore no query, and no comparison, that separates them.

## Decision

Ask the caller. This is the resolution path for unqueryable ambiguity — not a fallback of
last resort.

**The question must be phrased in the caller's vocabulary, not the catalog's.**

- Unanswerable: "Would you like Skin Cancer Screening or Full Body Skin Exam?"
- Answerable: "Is this one spot you're worried about, or a full head-to-toe check?"

Same two options; only the second is a question a patient can act on.

**Fallback when the caller still cannot choose:** *propose* the safer default, state which one it would
be, and require an explicit yes before writing. Never leave the choice implicit.

*(Corrected after round-2 review.)* The original wording — "pick the safer default, state which one was
booked" — contradicted our own commit gate: no booking from an undiscriminated set without an explicit
choice. Booking the default directly would be rejected by the gate we designed. The fallback therefore
costs one extra turn and keeps the rule intact.

## Rationale

Asking is cheap — one extra turn — and it removes the need to guess. But asking in catalog
terms is worse than not asking: it transfers an internal distinction onto a patient who has
no basis for it, wastes the turn, and makes the agent sound like a form.

This is also why asking does **not** remove the need for the meaning layer: it *consumes*
it. The lexicon is what turns a pair of near-identical catalog entries into one answerable
question, and it is the same lexicon that maps "my knee" onto a specialty. The two
requirements share one artefact.

## Consequences

- every unqueryable pair needs a *distinguishing question* authored alongside its synonym
  entry — the lexicon's schema gains a field
- the pair's safer default must be marked, so the fallback is not improvised per call
- "did it ask an answerable question?" becomes an eval metric, not a matter of taste
- the store stays out of it: structure cannot fix this, only meaning can

## What would reverse this

A catalog where near-duplicate types carry a real discriminator (an indicator, a
population, a duration difference), which would move them back into the queryable class.
