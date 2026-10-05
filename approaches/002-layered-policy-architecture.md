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
