# Decision 005 — Semantic layer: Postgres + pgvector, ranking inside a constrained set

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** the SQLite lean in approach 001 · **Superseded by:** —

## Context

A caller says "skin check". Mapping that to catalog vocabulary currently needs either an
authored lexicon (exact, but only covers phrases someone wrote) or an LLM word-expansion
round trip (300 ms–2 s, nondeterministic, in the voice path). The latter is the latency
problem: we do not want the model generating search variants on every utterance.

## Decision

**Store:** Postgres + pgvector. This flips the earlier SQLite lean, and the reason is not
vector scale — it is that Postgres gives lexical search, joins and vectors in one ACID store
with a real planner, plus row-level security for tenancy.

**Embeddings rank inside an already-constrained candidate set — never retrieve from the
whole catalog.** The query is graph/constraint first, vector second:

```sql
SELECT t.id, t.name, e.embedding <=> :q AS distance
FROM provider_appointment_types pat
JOIN appointment_types t ON t.id = pat.appointment_type_id
JOIN type_embeddings  e ON e.type_id = t.id
WHERE pat.provider_id = :provider            -- graph decides eligibility
  AND /* capability + patient gates */        -- validators decide admissibility
ORDER BY e.embedding <=> :q
LIMIT 5;
```

This ordering matters because "top-k then filter" silently loses recall — the surviving rows
may not be in the top-k at all. pgvector 0.8+ adds **iterative index scans** for exactly
this (`SET hnsw.iterative_scan = relaxed_order`, capped by `hnsw.max_scan_tuples`), so the
index keeps walking until enough rows survive the `WHERE` clause.

**Three retrieval modes, one query, fused:**

- graph/join — exact, index-driven (measured 0.017 ms anchored)
- `tsvector` — lexical and fuzzy, replacing the FTS5 role (handles "Dr. Ngyuen")
- pgvector — semantic, for phrasing the lexicon has never seen

Fuse with reciprocal rank fusion. An **exact alias table stays** for high-frequency phrases:
it is free, instant, and it protects the demo from an embedding-model outage.

**Embed an augmented string at ingest** — name plus specialty plus aliases — not the bare
label. Two-to-four token labels embed poorly, and clinical shorthand is underrepresented in
general embedding models.

**A near-tie means ask.** The distance gap between the top two is the ambiguity signal that
drives decision 004: "skin check" lands near both Skin Cancer Screening and Full Body Skin
Exam, because near-identical text produces near-identical vectors. The semantic layer
*discovers* the ambiguity cheaply; it does not remove it. Never take the argmax of a near-tie.

**Pin `embedding_model` alongside the policy set.** Changing the model changes decisions, so
re-embedding is a re-verification event like a catalog change. Cosine thresholds are
versioned policy, not constants buried in code.

## Measured (real catalog, `text-embedding-3-small`, all 82 types)

The claims in this decision were run against real data. Two survive, two need correcting.

**Confirmed — the top1−top2 gap is a usable ambiguity signal.** Cosine gap, median:

- phrases a human would hesitate on (*annual checkup, blood test, skin check, heart tracing
  test, allergy test, vaccine, child checkup*): **0.098**
- phrases with one obvious answer (*mammogram, psychiatric evaluation, flu shot, colonoscopy*):
  **0.273**

Clean separation, and the ordering is sane — *annual checkup* is the most ambiguous at 0.035,
which is exactly the Annual Physical / Annual Wellness Visit pair. So the threshold driving
"ask instead of guess" is **measurable rather than hand-tuned**. This is the strongest result:
decisions 004 and 005 lean on that number and it holds.

**Confirmed — rank, never trust top-1.** recall@1 **15/20 (75%)**, recall@3 **19/20 (95%)**.
The right candidate is nearly always in the top three; the *ordering* is where it fails. That is
precisely the shape this decision assumes: rank inside a constrained set, never book the top hit.

**Corrected — near-duplicates are similar, not near-identical.** Measured cosine:

```
Skin Cancer Screening  / Full Body Skin Exam      0.591
Annual Physical        / Annual Wellness Visit    0.634
Eye Exam               / Comprehensive Eye Exam   0.733
MRI - Brain            / MRI - Spine              0.753
Follow-up Visit        / Follow-up Consultation   0.821
Dental Exam            / Dental New Patient Exam  0.839
```

Similar enough to crowd the top of a ranking, but 0.59 is not "near-identical" as claimed above.
The effect holds; the stated mechanism was too strong.

**Not demonstrated — augmentation.** Bare labels and augmented strings scored **identically**
(15/20 and 19/20). The claim that augmented text embeds better is untested, not supported.
Re-test with real tenant aliases before relying on it.

**New finding — embeddings do not solve symptom → service.** "my knee has been hurting" ranks
**MRI - Knee** first, at an absolute similarity of only **0.424**, while everything else in the
set scored 0.6–0.96. So the meaning layer still needs the lexicon for symptoms, and the
**absolute** score — not only the gap — is a signal that the phrase is out of distribution.

**Methodological caveat.** Some test phrases nearly quote the labels ("I want a full body skin
exam"), which flatters recall. A stricter set would use layperson phrasing only.

## What this does not do

Admissibility. Similarity and validity are orthogonal — the nearest type may need a referral
the patient lacks, or a capability the location does not have. Vectors order candidates; the
validators still decide, and no booking is ever written on a similarity score.

## Consequences

- Postgres replaces SQLite; sqlite-vec exists but at GA is brute-force only, with ANN still
  in alpha — and it gives no hybrid or tenancy story
- `type_embeddings(type_id, model, version, embedding)` as its own table, so re-embedding
  never rewrites the catalog
- the lexicon shrinks from "cover every phrase" to "cover the frequent ones"
- eval gains a metric: recall of the semantic layer against unmapped phrasing

## What would reverse this

A local-only or no-server constraint. At 82 — or even 2,000 — vectors, brute-force
sqlite-vec is numerically adequate; the decision rests on hybrid search, planner and
tenancy, not on vector scale.
