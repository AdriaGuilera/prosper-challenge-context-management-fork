# Review — round 1: blind external panel

**Date:** 2026-10-05
**Method:** the same prompt to four models, none of which saw `approaches/`, `decisions/` or `eval/`.
Each received `task.md`, `backend/data/README.md`, the full `catalog.json`, `schema.py`,
`builder.py`, `bot.py` — and was asked for its own design from first principles.
Proposals saved verbatim under `/tmp/panel/`. Repo read-only.

| model | latency | cost | notes |
|---|---|---|---|
| `xiaomi/mimo-v2.6-flash` | 422 s | $0.007 | reasoning-heavy |
| `z-ai/glm-5.3-flash` | 72 s | $0.002 | needed `reasoning_effort=low` |
| `moonshotai/kimi-k3` | 70 s | $0.102 | needed `reasoning_effort=low` |
| `xiaomi/mimo-v2.6-pro` | 444 s | $0.013 | needed `max_tokens=32000` |

**Methodological caveat:** three of four returned *empty content* at `max_tokens=6000` — reasoning
consumed the whole completion budget and `finish_reason` was `length`. Each needed a larger cap or
`reasoning_effort=low`. A panel scored at a fixed token budget would have zeroed three models.

## Verdict: nothing fundamentally better was missed

All four independently arrived at the architecture we did, without seeing it. In their own words:

- "This is a **structured query + constraint satisfaction** problem, not a retrieval problem."
- "The catalog is a **database, not a prompt**." (twice, independently)
- "Not a retrieval problem over text — constraint satisfaction over a small relational graph."

All four converge on: a deterministic constraint layer in code; the LLM seeing only pre-filtered
valid triples; **ids never names** as keys; mandatory disambiguation; policies compiled into code
rather than prompt text; a measured baseline against the full-dump; an eval harness; and detection
of the unbookable appointment types. Two independently spotted that patient state (new / referral
on file) must be mocked, and that re-validation must happen at commit.

That is external validation of the spine. The panel's value is in the details, several of which are
better than ours.

## Worth adopting

1. **Materialise the valid-triple relation** (`booking_options`) rather than only joining on demand.
   The argument is correctness, not speed: invalid combinations become *unrepresentable* rather than
   *filtered*. Cheap at this scale (a few hundred rows). Our "index the edges, don't materialise"
   reasoning still holds at target scale, so: materialise as a derived read model, keep the join as
   the definition of truth.
2. **Registered `ambiguity_clusters`** — members are never auto-resolved, regardless of any score.
   Data-driven, and it subsumes the embedding-distance threshold as the runtime trigger.
3. **Idempotency key plus pinned `catalog_version`** on the booking write. Voice calls retry.
4. **Each offered option carries the checks that justified it** (referral ok, capability ok,
   new-patient ok) — the explanation comes free with the option instead of being reconstructed.
5. **Ground truth generated from intent specs, not hand-labelled** — the gold answer *and* the gold
   clarification count are computed by the same engine. Strictly better than our examples-confirmed-
   by-a-human design.
6. **Over-asking rate** (clarifications ÷ minimum necessary) as a first-class metric. Asking five
   questions is a quality failure too.
7. **`languages` is unused data with obvious value** — "a Spanish-speaking dermatologist" resolves
   from a column nobody would think to use. Free demonstration that the whole schema is read.
8. **A Pipecat Flows cost trap we had not noted:** node `task_messages` are re-injected on *every*
   transition, so catalog text baked into a node is paid for again at each edge. Keep nodes
   catalog-free; facts travel as tool results and state.
9. **Test design:** minimal pairs as the load-bearing cases; hold out paraphrases never seeded in
   the alias table (that is the open-vocabulary recall test); three seeds per case with Wilson
   intervals rather than a single pass.

## Panel consensus against us

**Alias table before embeddings.** Three of four: a curated synonym table beats a vector store at this
scale; add embeddings only past a few hundred types, and then as *recall* feeding the resolver,
never as the final ranker. That agrees with our own measurement better than decision 005 did —
augmentation showed no benefit, recall@1 was 75%, and embeddings did not solve symptom → service.
See decision 009.

## What none of them raised

- **Multi-tenant id collisions** — `appt_002` meaning different things in different payloads
  (decision 008). Not asked, so not a fair miss, but nobody raised it even under "scalability".
- **Policy as versioned data, the IR, and the multi-source onboarding pipeline** (decision 001).
  All four stayed inside the single given catalog — which is what the brief actually asks for.

**That second point is a scoping signal, not a validation.** Our design is a platform; the brief
grades a solution. Risk to hold in view: parts of our machinery may be over-built relative to the
stated criteria, and the panel's simpler designs would score equally well on them.

## Caveat on their details

Id-level claims contain errors — one proposal asserts eight providers surnamed Chen where we measured
seven — and none of them could verify ids against the file. Prose is signal; their literal ids are noise.
