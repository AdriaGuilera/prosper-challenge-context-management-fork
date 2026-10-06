# Approach 002 — Layered policy architecture

**Status:** candidate solution, not yet chosen
**Phase:** 2 (context management)
**Builds on:** `approaches/001-normalized-store-and-validators.md`
**Evidence:** all measurements below were run against the real catalog

---

## The claim

No single language carries this. The design is a stack where each layer does the one
thing it is measurable at, and the layer that holds the *rules* is chosen for
reviewability rather than for execution speed.

```
hospital JSON
   │
   ├─ 1. INGEST      map + validate → canonical store + data-quality report
   │
   ├─ 2. RETRIEVAL   SQL.  tenant-independent, written once, tuned once
   │
   ├─ 3. POLICY IR   structured rules — the source of truth, reviewable by clinic ops
   │
   ├─ 4. COMPILE     IR → CEL.  reference resolution doubles as data-gap detection
   │
   ├─ 5. VERIFY      check · statistical sanity · examples · differential · conflicts
   │
   └─ 6. RUNTIME     algebra around CEL · reasons · audit · hot-path caching
```

Plus two optional components that are not policy: slot selection (CP-SAT) and the
meaning layer (lexicon, or a decision model like Jev).

---

## 1. Ingest

Two different normalisations, routinely conflated:

- **structural** — arrays into edge tables, `required_capability` absent → nullable
- **semantic** — their vocabulary onto ours (`services: ["radiology"]` → `capabilities: ["imaging"]`)

Collapse them and a vocabulary mismatch silently drops an enum value. Output is a
**report**: referential integrity, orphan detection, vocabulary coverage, cardinality.
That report is the deliverable the hospital reads.

## 2. Retrieval — SQL

Tenant-independent: every hospital has the same *shape* of problem, so this layer is
written once.

- index the **edges**, don't materialise triples — 518 edges derive 760 valid triples
- **force anchoring** (≥1 anchor + `LIMIT`). Anchored: 0.017 ms. Unanchored `COUNT(*)`:
  64 ms at 78,784 rows and it computes the cross product.
- **join availability last** — narrow → rank → then slots. Joining first multiplies
  78,784 triples by every slot (≈1.5M rows).
- scale measured: 5,000 providers / 54,645 edges / 332 ms ingest, anchored queries flat

## 3. Policy IR — the source of truth

Structured rules, not SQL strings and not CEL as the stored form. CEL is a build
artifact.

Per rule: `id`, `tenant`, `effect` (permit/forbid), `scope`, `references`, `predicate`,
**`absence_semantics`** (deny / escalate / permit-with-flag), `message`, and
`tier` (structural / contextual / judgment).

An LLM *drafts* rules from hospital prose. Everything after that is deterministic.

## 4. Compile

IR → CEL, with a reference-resolution pass first. That pass is the point: a rule citing
`referral.expires_at` when no such field exists is a **data gap**, not a syntax error —
and it is the more common failure. The compiler doubles as a data-gap detector.

## 5. Verify — cheapest layer first

| layer | catches |
|---|---|
| check phase | malformed rules, type errors (measured: 4/4 bad rules failed loudly) |
| statistical sanity | rules firing on 0% or 100% of entities — **the wrong-but-valid rule** |
| examples | human confirms 3 positive + 3 negative examples, **not CEL** |
| differential test | CEL vs an independent implementation (measured: 760/760, exact) |
| fire-matrix conflict pass | opposite-effect rules co-firing on the same target |
| Z3 / SymCC-style | only for a *named* invariant, not the whole set |

Then **version + pin**: `policy_set_version` bound to `catalog_sha`. A data upload
invalidates the verification evidence, so re-verify on every ingest.

## 6. Runtime

- **algebra** — default deny, forbid overrides permit, **error → escalate** (not
  Cedar's skip-on-error, which fails *open* for forbids)
- **reasons** — the matched rule's `message`, produced by the evaluation, not
  reconstructed from the transcript
- **absence** — declared per rule, enforced at compile time: every reference is
  `required` or `optional`, and an unguarded optional fails compilation
- **audit** — one row per decision: `policy_set_id`, decision, matched rule, `now`,
  catalog version
- **hot path** — narrow-then-check; cache by `(policy_version, type_id, patient_class)`.
  Measured: ~8 ms for 2 policies × 6 candidates; 80 ms across all 82 types is not viable

---

## Data model

Four tiers. Note that every child table carries the catalog version, so history is kept by
append and any past booking can be explained against the data in force at the time.

**1. Source payload** (what a hospital uploads) — exactly the shape we were given: four
top-level arrays, string ids, prose policies inside the payload. Two things are *absent*
and must be supplied: patient state and availability.

**2. Canonical store** — normalised, per-source, versioned:

```sql
catalog_versions(id, source_id, source_sha, ingested_at, report_json)

locations(id, catalog_version_id, name, address, city, phone, hours)
location_capabilities(catalog_version_id, location_id, capability)          -- PK(...)
providers(id, catalog_version_id, name, title, specialty, accepting_new_patients)
provider_languages(catalog_version_id, provider_id, language)              -- PK(...)
provider_locations(catalog_version_id, provider_id, location_id)           -- PK(...)
appointment_types(id, catalog_version_id, name, specialty, duration_min,
                  requires_referral, new_patients_allowed, required_capability)
provider_appointment_types(catalog_version_id, provider_id, appointment_type_id)  -- PK(...)
```

**3. Policy IR** — rules as data, the source of truth. Not SQL, not CEL:

```sql
policy_sets(id, source_id, catalog_version_id, version, published_at, immutable)
policy_rules(id, policy_set_id, rule_id, effect, scope, predicate_ir,
             absence_semantics, message, tier, source_text, examples_json)
```

`source_text` is the hospital's original prose; `examples_json` holds the confirmed
positive/negative examples that became the regression suite.

**4. Patient state and mocked availability** — supplied, not given:

```sql
patients(id, is_new)
referrals(patient_id, appointment_type_id, issued_on, expires_on)

schedules(id, provider_id, location_id, weekday, start, end)   -- the shape the repo dropped
```

### Example rows (real data)

`providers` — note two different humans, one name:

```
id        catalog_version  name              title  specialty         accepting_new
prov_002  catalog_v1       Dr. Maria Garcia  MD     Pediatrics        1
prov_003  catalog_v1       Dr. Maria Garcia  NP     Family Medicine   1
```

`provider_locations` / `provider_appointment_types` — the graph, which is the authority:

```
prov_000  loc_000      prov_000  appt_020   (Cardiology Consultation)
prov_000  loc_001      prov_000  appt_021   (Echocardiogram — required_capability=imaging)
prov_000  loc_004      prov_000  appt_022   (Stress Test)
prov_000  loc_007
```

`location_capabilities` — which of Dr. Chen's four sites can host an echocardiogram:

```
loc_000  lab, physical_therapy, surgery          → cannot host appt_021
loc_001  imaging, lab, physical_therapy, dental  → can
loc_004  imaging                                 → can
loc_007  surgery                                 → cannot host appt_021
```

`absent` is not `null` — `required_capability` is simply missing on 65 of 82 types, which
is why absence semantics are declared per rule rather than inferred.

### Change classes — not everything versions the same way

Splitting by *change rate*, not by entity (decision 003):

- **structural** — providers, types, capabilities, relations. Rare change. Versioned by
  `catalog_version`. Policy-relevant, so a change triggers re-verification.
- **operational** — opening hours, closures, capacity. High churn, own table with
  `valid_from` / `valid_to`, separate write path, and **no** re-verification.

```sql
location_hours(location_id, weekday, open_min, close_min, valid_from, valid_to)
closures(location_id, reason, starts_at, ends_at)
```

`hours` today is the free-text string `"Mon-Fri 8:00-17:00"` on all eight locations — it
must be parsed at ingest into intervals, or "are you open on Sunday?" is unanswerable and
"answering questions about locations" is unimplementable.

`accepting_new_patients` sits in both classes: high churn *and* policy-relevant (policy 5).
It gets validity windows **and** is the one operational field whose change invalidates
verification.

## What is guaranteed, and what isn't

A guarantee means a deterministic layer enforces it and a test proves it — not that a
prompt asks nicely. Three grades.

**Hard — validators, enforced at commit, no LLM in the path**

- *Duplicate provider names.* Ids are the only keys (`providers.id` unique, 50/50); name
  resolution returns a **set**, and a booking from an undiscriminated set is rejected.
  Measured: 3 exact full-name collisions covering 6 providers, and 12 surnames shared by
  more than one provider — Nguyen ×8, Chen ×7, Sato ×6.
- *Location-gated services.* The capability join. 760/760 valid triples, 37 invalid ones
  rejected. A booking at a location lacking the capability cannot be written.
- *Multi-location providers.* The location is a required component of the booking key.
  34 of 50 providers practise at more than one site (2 of them at four).

**Conditional — invariants of the data: assert at ingest, never assume**

- "Every gated type a provider offers has a viable location." Holds here: 0 of 31 gated
  (provider, type) pairs lacks one. But it is a *property of the data* — the repo had to
  add commit `4467d93` to establish it. Assert it at ingest and report violations.
- Type-level orphans are a separate check that invariant does not cover: 8 types no
  provider offers at all, including the two capability-gated Physical Therapy types. That
  is how the commit's claim and the shipped data disagree — the gap is at type level, not
  provider level.

**Soft — score it; don't pretend to guarantee it**

- *Near-duplicate types.* For Annual Physical vs Annual Wellness Visit the data carries no
  signal at all — identical referral flag, identical `new_patients_allowed`, identical
  duration. Worse, **21 of 50 providers offer both**, so for 42% of providers the graph
  cannot decide either. Guaranteeable: never choose silently, and prefer the graph when it
  does decide. Not guaranteeable: mapping caller language onto the pair when both are
  offered.
- *Over-asking.* 30 of 31 gated (provider, type) pairs have exactly **one** viable
  location, so asking the caller there is a defect rather than diligence. That is a quality
  metric, not an invariant — invariants prevent invalid bookings, not annoying ones.

**Ambiguity is a query problem — where there is something to query on**

If names collide but other columns separate them, the agent doesn't guess and doesn't
need to ask blindly: its own query returns the discriminating attributes. Measured across
the 12 colliding surnames:

- **9 of 12 separate on (specialty, title)** alone — Nguyen, Sato, Garcia, Ramirez,
  Martinez, Hernandez, Rodriguez, Singh, Kim.
- The **3 remaining ties** are Chen (Cardiology, MD), Smith (Psychiatry, MD) and Patel
  (Internal Medicine, MD) — where specialty *and* title match. Each is still separable on
  a queryable column: first name, locations, languages, or `accepting_new_patients`.
- So **every** name collision is resolvable from the store, provided the query returns the
  discriminating columns with the candidates.

That reframes the requirement: normalisation doesn't remove the question, it makes the
question **answerable and closing** — "is that the dermatologist or the cardiologist?"
only works because specialty is a column. The selection policy follows: return candidates
*with* their discriminators, ask the one question that splits the set, and when the set is
already a singleton, don't ask at all.

**Except where there is nothing to query on.** The near-duplicate *types* have no
discriminator at all:

```
Annual Physical        vs Annual Wellness Visit   identical on 4 fields, differs only on specialty
Skin Cancer Screening  vs Full Body Skin Exam     identical on 4 fields, differs on NOTHING
School Physical        vs Sports Physical         identical on 4 fields, differs on NOTHING
```

And the graph can't resolve it either — a provider is given the whole block for their
specialty, so a Dermatology provider offers both skin types, and a Pediatrics provider
offers both physicals. There is no query that separates them, and `specialty` is not a
question a patient can answer. This residual gap is closed by the lexicon or by a question
about intent — not by the store. It is the honest limit of the query approach, and it is
worth stating rather than discovering in a demo.

The mechanically checkable version of all this: a resolution step either returns a
**singleton**, or returns a set **with an askable splitter**. If it returns neither, that's
a case for the lexicon — and a coverage metric.

**The four bullets describe *this* catalog, not catalogs.** Under decision 002 a new
payload may have zero collisions or ten. So the eval must derive its traps from the
ingested data at test time — detect collisions, near-duplicates, orphans, and
forced-vs-ambiguous gates programmatically — instead of hardcoding "Dr. Nguyen ×8", or the
regression suite quietly goes vacuous against the next payload.

## Components that are explicitly *not* policy

- **Slot selection → CP-SAT (OR-Tools).** "Soonest / preferred provider / preferred
  location" is combinatorial optimisation. `ORDER BY` is greedy; conflicting
  preferences need a solver, and infeasibility needs an explanation. Calibration: a
  published residency scheduling model with 17,010 Boolean variables reached OPTIMAL in
  1.65 s.
- **Meaning layer → lexicon, or Jev.** The catalog has *zero* symptom vocabulary, so
  "my knee" → Orthopedics has to come from somewhere. A decision model fits the
  classification shape well (typed output, 70–500 ms, calibrated confidence → the
  threshold for "ask instead of guess"). Constraint: Jev's Choice primitive caps at 255
  options, so it suits coarse routing (specialty, ~20) and not picking among 1,000
  providers or types.

## Why Jev is not in the policy slot

From TypeSafe's own documentation: *"Calibration is measured across groups of
predictions; it does not guarantee that an individual answer is correct."* A
well-calibrated wrong answer is still a wrong answer. Also: it decides over state you
supply, so using it for admissibility puts the catalog back into a prompt — it
recreates the context problem rather than solving it.

---

## Rejected alternatives, and why

| alternative | verdict |
|---|---|
| **Full-context dump** | only as a measured baseline. ~8k tokens now, ~179k at the stated target scale — past gpt-4o's window entirely. Failure mode today is silent collision, not hallucination. |
| **Vector RAG over catalog entries** | weak fit. Embeddings cannot express `accepting_new_patients AND has imaging AND type ∈ provider's set`, and near-duplicates are precisely what they conflate. Keep only as a lexical bridge. |
| **Custom query DSL** | the instinct was right, aimed wrong. SQL is the most-seen query language on earth; an invented dialect is unseen, unoptimised, and fails silently. |
| **SQL as the policy artifact** | capable but not accountable — see decision 001. |
| **Cedar** | brings the algebra, schema validation and SymCC proofs, but forces principal/action/resource framing, loses CEL's general expressions and ecosystem, and has no first-party Python path. Its skip-on-error also fails open for forbids. |
| **Sandboxed generated code / WASM** | maximum expressiveness, unbounded and unverifiable. Wrong trade for the enforcement slot. |

---

## Open decisions

1. **Cel-python in the hot path, or compile the structural subset to SQL?** The
   differential test licenses the optimisation; the cost is giving up the checker and
   cost model at runtime.
2. **How much conflict detection, and where?** Structural + empirical fire matrix is the
   cheap 80%; Z3 only for named invariants.
3. **Is the IR the persisted policy set** (with CEL purely a build output)? Leaning yes —
   it's what a hospital's ops team can review and diff.
4. **Build the schema-mapping layer, or treat the given JSON shape as the contract?**
   Leaning: contract first, mapping as explicit out-of-scope.

## Evidence summary

- normalised store: 79 ms ingest, 518 edges → 760 valid triples, anchored query 0.017 ms
- generated CEL vs hand-written SQL: **760/760 identical** over 797 raw triples
- malformed rules: 4/4 failed loudly — zero silent wrongness
- cel-python cost: 188 µs floor per `evaluate()`, ~1 ms for macro rules
- determinism: 1 distinct result over 1,000 runs per rule; no state leakage across 800 interleaved evals
- hermeticity: `now`, `random()`, `env()` all unresolvable — ambient state is impossible
- absence: absent key raises; null-valued member returns silent `False`
- binding type trap: string dates compare lexicographically — non-padded and offset formats invert rules
