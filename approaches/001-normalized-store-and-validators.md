# Approach 001 — Normalised store + policies as executable validators

**Status:** candidate, not yet chosen
**Phase:** 2 (context management)
**Evidence:** validated in a throwaway spike against the real `backend/data/catalog.json` (see [Evidence](#evidence))
**Origin:** proposal that the LLM should *write queries* against a normalised store instead of reading the catalog as context, with the booking policies stored as validators

---

## 1. The idea in one paragraph

Normalise the catalog once at ingest into a small relational store with proper edge tables
and indexes. Translate the six booking policies from prose into executable predicates. The
LLM's job shrinks to two things it is genuinely good at: turning what the caller said into a
*query*, and turning the query's answer into speech. The model never sees the catalog — it
sees a handful of result rows. Correctness stops being a property of the prompt and becomes
a property of the database.

## 2. Why this shape

The catalog is 45.7 KB ≈ 11.4k tokens and fits in context today. That is not the design
constraint — **scale is**:

| | now | generator default | stated target |
|---|---|---|---|
| shape (loc/prov/types) | 8 / 50 / 82 | 30 / 150 / 82 | hundreds / ~1000 / ~2000 |
| catalog as context | ~8k tok | ~17k tok | **~179k tok** |

At the scale the challenge names, dumping the catalog is not expensive — it is impossible
(past gpt-4o's 128k window). And today's failure mode is not hallucination but **silent
collision**: eight providers named Nguyen in context, and the model books a real but wrong
one with full confidence.

Two costs fall out of the same move: tokens go from O(catalog) to O(results), and policy
enforcement goes from probabilistic to deterministic.

## 3. Data model

Normalise once, at ingest. Keep `catalog.json` **immutable as the input** — the store is
derived, so a larger catalog is an ingest problem, not a code change, and nobody can claim
the data was hand-shaped.

```sql
locations(id, name, city, address, phone, hours)
location_capabilities(location_id, capability)          -- PK(location_id, capability)

providers(id, name, title, specialty, accepting_new_patients)
provider_languages(provider_id, language)               -- PK(provider_id, language)
provider_locations(provider_id, location_id)            -- PK(provider_id, location_id)
appointment_types(id, name, specialty, duration_min,
                  requires_referral, new_patients_allowed, required_capability)
provider_appointment_types(provider_id, appointment_type_id)  -- PK(...)
```

Patient-side state is *not* in the catalog and has to be modelled:

```sql
patients(id, is_new)
referrals(patient_id, appointment_type_id)   -- policy 4 has nothing to point at otherwise
```

Indexes go **on the edges**, never on a materialised triple table:

```sql
CREATE INDEX ix_pl_loc   ON provider_locations(location_id);
CREATE INDEX ix_pat_at   ON provider_appointment_types(appointment_type_id);
CREATE INDEX ix_cap_cap  ON location_capabilities(capability);
```

### Three normalisation landmines

1. **`specialty` must never become a join key for eligibility.** The generator unions a
   generic primary-care block into every Family Medicine / Internal Medicine / Pediatrics
   provider, so 215 provider↔type pairs cross specialties by design — a Pediatrics provider
   legitimately offers `Annual Physical`, a Family Medicine type. `provider_appointment_types`
   is the authority; `specialty` is a display and routing label. Wire specialty into a
   foreign key and the correctness layer is wrong by construction — and it looks correct on
   a small sample.
2. **Do not materialise the (provider, location, type) triples.** 518 edges derive into 760
   valid triples today; at target scale that becomes millions of rows while the join stays
   cheap. Index the edges; let the planner intersect.
3. **`required_capability` is absent, not null** on 65 of 82 types. Nullable column, read
   with `.get()` / `IS NULL`, and never make it part of an index prefix.

## 4. Policies as validators

The six policies are already written at the right granularity to become predicates. Five
collapse into one view:

```sql
CREATE VIEW bookable AS
SELECT pl.provider_id, pl.location_id, pat.appointment_type_id
FROM provider_locations pl
JOIN providers pv                   ON pv.id  = pl.provider_id
JOIN provider_appointment_types pat ON pat.provider_id = pv.id
JOIN appointment_types at           ON at.id  = pat.appointment_type_id
WHERE at.required_capability IS NULL
   OR EXISTS (SELECT 1 FROM location_capabilities lc
              WHERE lc.location_id = pl.location_id
                AND lc.capability  = at.required_capability);
```

| policy | becomes | tier |
|---|---|---|
| 1. provider only at their locations | `provider_locations` edge exists | schema |
| 2. provider only for their types | `provider_appointment_types` edge exists | schema |
| 3. capability-gated types | `IS NULL OR EXISTS(location_capabilities)` | schema |
| 4. referrals | `EXISTS(referrals)` | schema (patient context) |
| 5. new-patient rules | `new_patients_allowed AND accepting_new_patients` | schema (patient context) |
| 6. multi-location disambiguation | caller explicitly chose a location | **state** |

**Policy 6 is the odd one out, and that is the useful part of the analysis.** It is not a
data predicate at all — it is a *dialogue-state precondition*. So validators come in two
tiers:

- **schema validators** — pure functions of the data and patient record; query-time, testable
  exhaustively against the whole catalog
- **state validators** — functions of the conversation (has the location been disambiguated?
  did the caller confirm?); commit-time

Generalising policy 6 gives the rule that matters most in practice: **no booking from an
ambiguous candidate set without an explicit caller choice.**

## 5. Query interface

**SQL plus a parameterised function, not a bespoke query language.**

Expose one bounded entry point rather than open SQL:

```
find_bookable(provider_id?, name_hint?, specialty?, location_id?,
              type_id?, type_hint?, is_new, has_referral, language?,
              limit=5) -> rows(provider, location, type, flags)
```

Whitelisted parameters, `LIMIT` enforced inside the function, index-friendly by construction.
The model emits a **tool call**, not a language.

### Why not a custom GraphQL-ish DSL

The instinct — *let the model write the query instead of reading the data* — is right; a new
language is the wrong implementation of it.

- **Generated-code reliability tracks pretraining exposure.** SQL is the most-seen query
  language there is. An invented dialect is by definition unseen: you pay prompt tokens
  teaching it every turn and you inherit its syntax errors. The economics are already won
  (see Evidence); there is no need to also win a grammar-design contest.
- **No optimiser.** SQL brings statistics, indexes and a planner. A DSL brings a Python loop
  over candidate sets. At hundreds of locations that difference *is* the scalability story.
- **Loud failure.** SQL fails with a syntax error or an empty set. A dialect that mostly
  works fails silently — and the expensive error here is silent.

What GraphQL genuinely buys — a typed schema to introspect and compose against — is already
available from the **JSON Schema on the tool signature**, which costs almost nothing in
prompt and which the model handles very well.

Practical bonus: SQLite ships **FTS5 / trigram** matching, so fuzzy and mis-transcribed names
("Dr. Ngyuen") resolve *inside the same query*. That keeps embeddings out of the voice
latency path.

## 6. The layers this approach does not replace

These are not optional extras — without them a flawless query engine answers the wrong
question precisely.

1. **Meaning layer.** The catalog contains *zero* symptom or service vocabulary. "My knee has
   been bad" → Orthopedics has to come from somewhere. Needs an authored lexicon: symptom and
   body-part → specialty/service, plus an alias table (~40 colloquialisms to start; production
   would learn it from transcripts, and it can seed from the annotated near-duplicate families
   in the catalog).
2. **Ambiguity as a first-class outcome.** `find_bookable` legitimately returns 8 individually
   valid Nguyens, or 3 answers across *two different Chens*. The system must return
   `ambiguous(candidates, discriminating_attributes)` and ask the single best discriminating
   question. Never let the model pick the argmax of an ambiguous set.
3. **Ranking layer.** SQL yields the valid *set*; "soonest available", "same language", "their
   usual site" is scoring on top of it. Hard constraints in SQL, preferences in the ranker.
   Mixing them makes both untestable.
4. **Availability.** Must be invented (recurring weekday blocks, matching the shape of the
   `schedules` table that was dropped from the repo) and joined **after** the valid set, never
   before.
5. **Latency discipline.** A query-writing agent loop is serial, and each round trip is one to
   two seconds of dead air in a voice call. Run the resolver in a Pipecat Flows `pre_action`
   before the model speaks and inject the result through `{{ state }}` templating, so the
   common path is one LLM turn. Cap retries.
6. **Commit-time gate.** A generated query is not a guarantee, and state goes stale mid-call.
   Re-run the validators at booking time, immediately before the write.

## 7. Evidence

Throwaway spike, real catalog, SQLite, nothing materialised:

```
INGEST: 79 ms   (8 locs, 50 provs, 82 types) -> 518 edge rows
bookable view: 760 rows, derived by join

"echo with Dr. Chen"        -> 3 valid answers in 0.26 ms
                               (Dr. David Chen @ loc_001 + loc_004, Dr. Emily Chen @ loc_004)
"Dr. Nguyen"                -> 8 candidates, each individually valid, in 0.09 ms
new patient, no referral    -> 33 of 82 types eligible, in 0.04 ms
advertised but undeliverable-> 8 types, detectable at ingest, in 0.12 ms
invalid (prov, loc, type)   -> 37 triples the naive design would happily offer, 0.22 ms

cost the model sees:  3-row resolver answer ~27 tokens  vs  catalog ~11,415 tokens
```

The `echo with Dr. Chen` result independently reproduces the manual walkthrough: Dr. David
Chen practises at four sites, only two of which carry the `imaging` capability the type
requires — so taking his first location produces an impossible booking.

**Spike verdict (Given/When/Then):** *given* `catalog.json` exactly as shipped, *when*
normalised into edge tables with the policies expressed as a view, *then* every hard question
resolves in sub-millisecond time with correct rows and O(results) context — **VALIDATED**.
The spike script lives outside the repo and is disposable; the DDL and view above are the
reproducible part.

## 8. How to prove it properly

- **Differential testing of the validator set.** Implement the policies twice — the SQL view
  and plain Python predicates — and run both across every raw combination in the catalog.
  All 797 raw (provider, location, type) triples must agree; 37 invalid, 760 valid. Two
  independent implementations that must agree is a correctness argument, not a vibe.
- **Regression set from the catalog's own traps.** The annotated near-duplicate families, the
  exact full-name collisions, the surname-only collisions, capability-gated services,
  referral and new-patient gates, the 8 undeliverable types, and the multi-location rule.
- **Scaling ladder.** The deleted `data/generate_catalog.py` is recoverable from commit
  `e9abc8a` and is deterministic (`seed=7`, knobs `N_LOCATIONS` / `N_PROVIDERS`). Regenerate at
  30/150 and at the few-hundred/thousand rung to test ingest and query time under growth.
- **Two metrics, reported together:** task success (right triple, or asked the right
  disambiguating question, or correctly refused) and cost (input tokens per turn, per
  successful booking, plus latency to first audio) — head-to-head against the naive
  full-context baseline.

## 9. Risks / open questions

- **Lexicon drift.** The symptom→specialty map is authored content and will be incomplete.
  What is the fallback when nothing matches — ask a clarifying question, or offer the
  generalist path?
- **Generated SQL is still generated.** Even with a parameterised function, the model chooses
  *parameters*; a wrong-but-valid parameter set returns a wrong-but-valid answer. The
  meaning layer is the mitigation, and it is the weakest link.
- **Storage choice.** SQLite is sufficient and honest (real indexes, real planner, no
  operations, demoable) and can be swapped for Postgres later. The scaling claim is about
  indexing, not about the engine.
- **Where does patient state live** for a demo? Mocked patient records with `is_new` and
  referrals, or an explicit "caller has no referral on file" assumption — needs a decision,
  because policies 4 and 5 are untestable without it.
- **Unrelated finding worth confirming:** the repo's own commit `4467d93` asserts that gated
  appointment types are always bookable, but `Physical Therapy Evaluation` and
  `Physical Therapy Session` are capability-gated, two locations carry `physical_therapy`, and
  no provider offers either type. Either intentional dead data or an artefact of the 150→50
  provider trim. The design must handle "we advertise it but cannot deliver it" either way.

## 10. Next step if adopted

1. Ingest into SQLite, keeping `catalog.json` untouched.
2. Implement the schema validators once (SQL view) and once (Python) — differential test.
3. Add the patient-state tables so policies 4 and 5 are testable.
4. Build the lexicon.
5. Wire `find_bookable` into a Flows `pre_action` with `{{ state }}` injection, so retrieval
   costs zero extra LLM turns.
