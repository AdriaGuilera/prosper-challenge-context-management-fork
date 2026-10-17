# Eval results

**Latest runs:** 2026-10-16/17 · **Harness:** `backend/evals/` · **Judge:** gpt-5.6-terra · **Caller:** gpt-4o

Run directories (`eval/runs/<run id>`) are gitignored, so this file is the record. The long version, with every
earlier stage and experiment, is in git history (`git log -- docs/eval/results.md`).

## Summary

| | My agent (tools) | Baseline (full catalog in the prompt) |
|---|---|---|
| Pass, given catalog | **88–96%** | 24–57% |
| Pass, hidden callers, given catalog | **82–89%** | 32–67% |
| Pass, 9 other catalogs | **73–84%** | – |
| Pass, 300-provider catalog | **75%** | 10–25% |
| Conversations with a wrong statement, given | 0–3% | 19–59% |
| Prompt tokens per conversation, given → 300 providers | 22–33k → 27–31k | 80–91k → 379–515k |
| Largest single prompt, given → 300 providers | 2.4–2.8k → 2.7–3.0k | 8.6–9.0k → 32–34k |
| Cost per conversation, gpt-4o, given | $0.051 | $0.111 |

- The agent that sees less is more accurate and cheaper.
- Its prompt stays the same size when the catalog grows; the baseline's grows with the catalog.
- The design matters more than the model: gpt-4o-mini on tools ($0.004) beats gpt-5.6-terra on the baseline ($0.164).
- No booking ever broke a policy: `book_appointment` checks all 7 in code. A "wrong statement" is something the agent
  said, for example a time or a clinic no tool returned.

## How it is tested

- **Caller.** gpt-4o plays a persona: a goal ("a flu shot near Sunset") and a behavior (cooperative, vague,
  terse-impatient, rambler, correction-hunter, adversarial). The same caller model is used for every agent model.
- **Answer key.** Code computes it per catalog from the catalog, the policies, the calendar and the map: which visits
  fit, which bookings are valid, what the agent must ask or say.
- **Judge.** gpt-5.6-terra reads the whole call (speech, tool calls, bookings written) and compares it with the
  answer key. Pass = no wrong statement, no missed requirement, and an outcome at least "acceptable".
- **Callers.** 20 hand-written plus up to 12 generated per catalog from its own traps (name collisions, referral and
  new-patient gates, closed days, far places). The **hidden set** is 44 callers written by gpt-5.6-terra from the
  catalog alone (`eval/personas-hidden/`, `--personas hidden`); it was never used to tune the agent.
- **Catalogs.** `given`; 6 generated (small, large with 300 providers, collisions, gates, sparse, odd hours); 3
  adversarial (twins, referral maze, near-duplicate names).

## Runs

### Given catalog, 4 models (`20261017-183559-nocap-dev`, `-nocap-hidden`; baseline from `20261016-000802-fixed-stage1-given`, `20261016-010436-hidden2-given-regraded`)

| Model | Tools, my callers | Tools, hidden | Baseline, my callers | Baseline, hidden | $/conv tools / baseline |
|---|---|---|---|---|---|
| gpt-4o | 88% | 86% | 24% | 40% | $0.051 / $0.111 |
| gpt-4o-mini | 89% | 82% | 42% | 32% | $0.004 / $0.007 |
| gpt-5.6-luna | 96% | 89% | 46% | 49% | $0.006 / $0.017 |
| gpt-5.6-terra | 93% | 88% | 57% | 67% | $0.052 / $0.164 |

74 conversations per row for my callers (37 cases × N=2), 88 for hidden (44 × N=2).
Wrong statements: tools 4/296 and 4/352; baseline 105/296 and 72/352.

### 9 other catalogs, tools only (`20261016-000804-fixed-stage2-variants`, `20261016-010438-hidden2-variants-regraded`)

| Callers | Model | All | near-dupes | referral-maze | twins | collisions | gates | large | oddhours | small | sparse |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Mine | gpt-4o-mini | 73% | 48 | 74 | 86 | 66 | 83 | 57 | 80 | 86 | 80 |
| Mine | gpt-5.6-luna | 84% | 64 | 86 | 83 | 86 | 91 | 89 | 91 | 89 | 77 |
| Hidden | gpt-4o-mini | 69% | 43 | 64 | 70 | 70 | 77 | 68 | 73 | 75 | 77 |
| Hidden | gpt-5.6-luna | 78% | 41 | 73 | 73 | 93 | 91 | 82 | 89 | 77 | 80 |

Wrong statements: 9/626 and 17/792. The hidden callers were written for `given`, so on other catalogs they sometimes
ask for doctors and visits that do not exist there, which explains part of the 4–6 point gap.

### 300-provider catalog, 10 hard cases (`20261016-000806-fixed-stage3-scale`)

| Model | Tools | Baseline | Prompt tokens/conv, tools / baseline |
|---|---|---|---|
| gpt-4o | 75% | 10% | 28k / 398k |
| gpt-4o-mini | 75% | 10% | 27k / 515k |
| gpt-5.6-luna | 75% | 25% | 31k / 379k |

Wrong statements: tools 0/60, baseline 29/60.

## Experiments that shaped the design

| Experiment | Result | Decision |
|---|---|---|
| Agent writes its own SQL against the schema (`20261007-072023-sql-experiment`) | 20–36% pass vs 92–100% for tools; 272 of 833 queries returned nothing | Removed. Fixed questions as tools, not the schema. |
| Type search: aliases + full text + vectors vs embeddings + Decisions API (offline, 72 phrases) | right 48/72 → 71/72 | Embeddings + Decisions; alias tables and lexicons deleted |
| Rule-based judge vs LLM judge (150 hand-checked conversations) | gpt-5.6-terra agrees 87% and was right in 17 of 20 disagreements; gpt-4o 69% | LLM judge, gpt-5.6-terra |
| Policies as one function each (`20261011-181219-policyset`) | no regression; full policy check on 300 providers in 11 ms | Kept |
| Drop the `capability` argument of `find_locations` (`20261017-183559-nocap-*`) | used 5 times in 468 calls, always in place of the visit; no regression | Removed |

## Eval defects found and fixed (2026-10-16)

- The answer key required a question ("which part of Geary?") even when nothing could be booked.
- Look-alike visits: only MRI must ask (different body part); skin and annual visits accept either.
- The judge counted a name the caller said first ("Dr. Chen") as invented by the agent.
- The answer key gave new patients referrals that the test harness never stores.
- The answer key crashed on a location name missing from a catalog.
- The first hidden set put requirements in the answer key the caller never says; the generator now rejects them.

Re-grading the same transcripts after these fixes moved pass rates up 4–15 points, so earlier numbers were partly
eval errors.

## Known gaps

- **MRI.** "An MRI for my back" is booked without asking which MRI: 0/8 on `given`.
- **Near-duplicate names.** `adv-near-dupes` is the weakest catalog (41–64%).
- **Wrong day read back.** Once, gpt-4o-mini read back "tomorrow" for a Wednesday booking.
- **Misheard provider names.** Spelling and sound matching find "Garsia" and "Chan", not "Nwen" for Nguyen.

## Limits

- **No audio.** Speech-to-text errors are simulated only by persona wording ("Gary" for "Geary").
- **One caller model** (gpt-4o). Another would shift absolute numbers; comparisons hold because it is fixed.
- **Small N.** N=2 on `given`, N=1 on the variants. The judge flips about 2–9 verdicts per run, so differences of a
  few points are noise.
- **Same model writes the hidden set and judges.** A mistake in how gpt-5.6-terra reads a case could appear in both.

Reproduce: `make db-up && make ingest CATALOG=all && make eval CATALOG=given MODELS=gpt-4o,gpt-4o-mini,gpt-5.6-luna,gpt-5.6-terra N=2 BASELINE=1`
