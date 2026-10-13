# Eval results: Phase 2 store and tools vs the naive baseline

**Date:** 2026-10-07 · **Branch:** `feat/phase2-backend` · **Harness:** `backend/evals/` · **Store:** `backend/store/`

Run everything again with `make db-up ingest CATALOG=all`, then `make eval …` (see the README). Run directories
are gitignored, so this file is the record. Every number below names its run id under `eval/runs/`.

## TL;DR

| | tools agent (this design) | naive full-catalog prompt |
|---|---|---|
| gate pass, given catalog, 4 models × 37 cases × 2 | **86–95%** | 35–62% |
| gate pass, 300-provider catalog (stage 3) | **80–95%** | 15–50% |
| prompt tokens per conversation, given → 300 providers | ~27k → ~28k (**flat**) | ~88k → 375–470k |
| largest single prompt, given → 300 providers | ~2.6k → ~2.8k | ~8.7k → ~32k |
| conversations with a safety-invariant break (given) | 0–1 / 74 | 16–34 / 74 |
| $/conversation, gpt-4o, given | $0.050 | $0.119 |

> **Judge note (2026-10-13).** Every pass rate in stages 1–4, the SQL experiment and the PolicySet check was produced by
> the rule-based judge, which has since been replaced by an LLM judge. On a 150-conversation sample the two agree on
> 87% of verdicts; the LLM is stricter, mostly on real mistakes the rules missed (see
> [The judge is now an LLM](#the-judge-is-now-an-llm-judge-agreement-20261013-192414)). Expect re-judged numbers to
> come out somewhat lower, the baseline and SQL agent more than the tools agent.

- Accuracy and cost move in the same direction: the agent that sees less is right more often.
- Its context does not grow with the catalog. The baseline's grows linearly.
- The model choice matters far less than the context design:
  - the cheapest model on the tools design (gpt-4o-mini, $0.004/conv) beats the most expensive model on the baseline (gpt-5.6-terra, $0.18/conv) by 24 points;
  - on tools, all four models land within 9 points of each other;
  - on the baseline they spread over 27 points.

## What was built and what the eval checks

Store (Postgres 17 + pgvector + PostGIS + pg_trgm + fuzzystrmatch):
- normalised tables namespaced by `catalog_version`;
- policies 1–5 as the `bookable` relation, and policy 6 as a commit-time "was this exact slot offered" check;
- a seeded calendar, with slots computed in SQL and joined last.

Verification:
- `make test` runs 417 tests in about 25 s.
- The differential test compares the SQL relation with an independent Python engine on every raw triple, in 6 patient states, across all 10 catalogs. The given catalog has 797 raw triples: **760 valid, 37 invalid**, identical on both sides.
- Scrambling every specialty label moves zero triples.
- SQL slots equal the Python reference.

Eval:
- **Text runner.** It reproduces Pipecat Flows semantics on the builder's own handler closures, so a text conversation exercises exactly the code a voice call does.
- **Caller simulator.** A fixed model (gpt-4o, T=0.6) plays the caller. It sees only the loader's briefing, which a leak tripwire test guards.
- **Oracle.** Each persona's ground truth is computed per catalog by the reference engine, the seeded calendar and the gazetteer. Nothing is stored per persona.
- **Gate.** At the time of these runs, a rule-based judge decided pass/fail (now replaced by an LLM judge, see below):
  - safety invariants: an invalid triple, booking without an explicit yes, booking without a read-back of the doctor/place and time, a provider offered at a site they don't work at, a time no lookup returned, a booking claimed that doesn't exist, the wrong patient record;
  - an outcome tier of acceptable or better;
  - required questions asked before booking;
  - required facts stated;
  - information answers.
- **LLM judge.** At the time, it scored quality only and never moved the gate.

Catalogs:
- `given`;
- six generated variants: small, large (40 locations / 300 providers / 122 types), collisions, gates, sparse calendar, odd hours;
- three hand-built adversarial catalogs: twins, referral maze, near-duplicate names.

Each catalog has its own gazetteer mapping, lexicon and patient registry.

Personas:
- 20 hand-written;
- up to 21 generated per catalog by mining its own traps: surname collisions, registered clusters, capability gates, referral gates (including expired referrals), new-patient gates, multi-location providers, orphans, closed days, proximity ties and far places;
- crossed with 6 behaviour profiles.

## Stage 1: given catalog, 4 models, tools vs baseline (`20261007-001245-stage1-given`)

592 conversations: 37 persona×behaviour cases × 4 models × 2 systems × N=2.

| system | model | gate pass | mean score | ideal/acc/sub/wrong | invariant breaks | LLM calls/conv | prompt tok/conv | max prompt | $/conv | turn p50/p95 ms | quality (1–5) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | gpt-4o | 38% | 0.46 | 54/12/0/8 | 34 | 10.6 | 88,083 | 8,640 | $0.119 | 2163/3714 | 4.65 |
| baseline | gpt-4o-mini | 35% | 0.49 | 41/10/2/21 | 18 | 11.5 | 95,564 | 8,671 | $0.008 | 1379/4336 | 4.65 |
| baseline | gpt-5.6-luna | 54% | 0.59 | 57/9/0/8 | 21 | 9.5 | 81,265 | 8,974 | $0.017 | 1978/4489 | 4.55 |
| baseline | gpt-5.6-terra | 62% | 0.68 | 59/9/2/4 | 16 | 10.4 | 87,712 | 8,798 | $0.180 | 1936/4372 | 4.73 |
| **tools** | gpt-4o | **92%** | 0.94 | 67/4/0/3 | 1 | 14.3 | 24,298 | 2,453 | $0.050 | 2547/4694 | 4.86 |
| **tools** | gpt-4o-mini | **86%** | 0.85 | 60/4/0/10 | 0 | 15.2 | 28,892 | 2,659 | $0.004 | 2487/4507 | 4.70 |
| **tools** | gpt-5.6-luna | **93%** | 0.91 | 62/8/0/4 | 0 | 14.4 | 27,554 | 2,672 | $0.006 | 2848/4881 | 4.74 |
| **tools** | gpt-5.6-terra | **95%** | 0.95 | 69/2/0/3 | 0 | 14.3 | 26,958 | 2,655 | $0.059 | 2883/4927 | 4.79 |

Notes on reading the table:
- "Mean score" is the outcome tier: 1 ideal, 0.7 acceptable, 0.3 suboptimal, 0 wrong; any invariant break scores 0.
- Tool time is included in turn latency. The tools' own p50/p95 is 16–20 / 107–288 ms. The p95 includes the embedding API call for an unseen phrase.

Prices are list prices per 1M tokens, input / output:
- gpt-4o: 2.50 / 10.00, cached 1.25;
- gpt-4o-mini: 0.15 / 0.60;
- gpt-5.6-luna: 0.20 / 1.20;
- gpt-5.6-terra: 2.00 / 12.00.

No cached-input discount is assumed for 5.6. Simulator and judge tokens are excluded.

**Where the baseline fails**, per the failure table in the run's summary.md:

1. **Booking without a read-back and an explicit yes: 47 of 296 conversations.** With everything in one prompt, nothing structural separates "the caller picked a time" from "the caller agreed to this exact booking". The tools design has a confirm node. Across 296 tools conversations, the final judge records 1 invariant break in total (a premature "you're all set").
2. **Not asking when it must.** On registered ambiguity clusters, the baseline passes 17–61% and the tools 78–100%. Examples:
   - "skin check": the baseline books one of the two near-identical types without asking. Sometimes the guess is the right one, and the gate still fails it.
   - "Dr. Chen" for an echocardiogram: the baseline picks a Chen.
3. **Offering providers at sites they don't work at.** One example: Dr. Kenji Kim "at Mission District Family Clinic". The data is in its prompt; the pairing is the error. The tools design can't do this: a slot exists only for a (provider, location) pair the relation admitted.
4. **Proximity and hours.** Proximity cases pass 0–50% on the baseline: it has addresses but no geometry. Hours cases pass 25–50%: it reads "Mon-Fri 8:00-17:00" correctly but misattributes which clinic is open.

**Where the tools agent fails** (given catalog):

- New-patient gates pass 33–67%. In gen-newpatient-1, every model said the named doctor "couldn't be found", because it searched with `accepting_new_patients=true`, which hid her.
  - Fixed in `3237b27`: a filter can no longer make a real provider vanish; the tool says who they are and why they don't fit.
- Children. "Book my son a checkup" was booked as an *Annual Wellness Visit* by 3 of 4 models: "checkup" maps to the adult pair, and the pediatrician's block includes it.
  - Fixed in `c9e5ad1`: `lookup_patient` returns the age, and for a minor the visit search also ranks inside Pediatrics. Specialty is used as a ranking signal, never as an eligibility key.
- gpt-4o-mini's specific weaknesses:
  - it claims a referral is missing when the lookup listed it;
  - once, it returned empty replies in a loop;
  - it misses the read-back.

## Stage 2: nine variant catalogs, tools agent, 2 models (`20261007-001411-stage2-variants`)

630 conversations: about 35 cases per catalog (hand-written plus generated) × gpt-4o-mini and gpt-5.6-luna, N=1. The hand-written personas run unchanged on every catalog. Their oracle changes per catalog. For example, "Dr. Chen for an echocardiogram" becomes "there's no Dr. Chen here" in gen-small, and "your referral expired" in adv-referral-maze.

| catalog | gpt-4o-mini | gpt-5.6-luna | invariant breaks |
|---|---|---|---|
| gen-small (3 loc, 12 prov) | 86% | 80% | 0 |
| gen-large (40 loc, 300 prov) | 89% | 94% | 0 |
| gen-collisions | 74% | 86% | 0 |
| gen-gates | 77% | 94% | 1 |
| gen-sparse | 77% | 86% | 0 |
| gen-oddhours | 83% | 89% | 1 |
| adv-twins | 80% | 86% | 0 |
| adv-referral-maze | 69% | 83% | 0 |
| adv-near-dupes | 77% | 57% | 0 |
| **all** | **79%** | **84%** | 2 / 630 |

- **Scale does not hurt.** gen-large (300 providers) scores as well as or better than the given catalog. The largest prompt is ~2.4k tokens on every catalog.
- **What hurts is meaning, not size.**
  - adv-near-dupes ("Consultation" / "Initial Consultation" / "Consult - Extended") is the weakest, for both models.
  - Asked about a knee with no orthopedics in that catalog, agents booked a general "Initial Consultation" without saying no specialist exists. That is a silent substitution, and the gate correctly fails it.
- **Hallucinated descriptions.** In adv-referral-maze, one conversation described a Family Medicine NP as "a pediatric nurse practitioner" to a parent who had asked for a pediatrician. The data was right; the model embellished it. The gate catches it only through the wrong-type outcome.

## Stage 3: scale, tools vs baseline on the 300-provider catalog (`20261007-002805-stage3-scale`)

10 hand-written personas, cooperative, × 3 models × 2 systems × N=2: 120 conversations.

| system | model | gate pass | prompt tok/conv | max prompt | $/conv | invariant breaks |
|---|---|---|---|---|---|---|
| baseline | gpt-4o | 15% | 382,515 | 31,901 | $0.490 | 10 |
| baseline | gpt-4o-mini | 20% | 469,632 | 32,016 | $0.037 | 4 |
| baseline | gpt-5.6-luna | 50% | 375,037 | 34,242 | $0.076 | 6 |
| tools | gpt-4o | 80% | 27,473 | 2,705 | $0.055 | 0 |
| tools | gpt-4o-mini | 85% | 28,334 | 2,668 | $0.004 | 0 |
| tools | gpt-5.6-luna | 95% | 30,169 | 2,860 | $0.007 | 0 |

The baseline's prompt grows about 4× from the given catalog to this one, and its pass rate falls with it: gpt-4o 38%→15%, gpt-4o-mini 35%→20%, gpt-5.6-luna 54%→50%.

These are the hardest hand cases. Many of them ask for something gen-large doesn't have (no Dr. Chen in cardiology, other Nguyens), so the right answer is a refusal followed by an alternative. The tools agent's prompt is the same size it was on 8 locations.

## The meaning layer: do embeddings earn their place? (`python -m evals.search_eval`)

Offline, with no LLM: 40 lay phrases ("my heart keeps racing", "I have the flu, I feel awful", "hello") and their acceptable types.

| type ranker | right | unsure (agent asks the caller) | wrong |
|---|---|---|---|
| alias + full text + **pgvector**, RRF | **29** | 9 | **2** |
| alias + full text only | 22 | 13 | 5 |

- **Without vectors, the lexical layer is confidently wrong.** "I have the flu" → Flu Shot; "broke my wrist, need it checked again" → Pacemaker Check; "annual exam for my health plan" → Annual Well-Woman Exam.
- **With vectors, "unsure" usually still points the right way.** In 8 of the 9 "unsure" verdicts, the right type is among the options the agent offers back as a question.
- **Measured thresholds, not guessed ones.**
  - Junk ("hello", "an appointment") and real lay phrasing ("sore throat and ears") both score 0.40–0.50 cosine.
  - So a weak best match only counts with lexical support plus a clear gap; otherwise the verdict is `unsure` instead of a confident wrong `match`.
  - Measured before setting the thresholds, see `a6b2834`: "appointment with Dr. Nguyen" came back as a confident match for New Patient Consultation.
- **Remaining wrongs:**
  - "I'm pregnant and need a checkup" asks Physical vs Wellness: two curated phrases fire and the generic one wins the cluster.
  - "my meds need adjusting, I see a psychiatrist" → Psychiatric Evaluation instead of Medication Management.
  - Both are lexicon fixes, recorded here rather than tuned away.

A live ablation (`--no-vectors`, gpt-4o-mini, given catalog) is in `20261007-010058-ablation-novectors`; see § Stage 4.

## How the approach changed because of the eval

Each change below was driven by a failing conversation, and each moved a number.

| run | result | what it exposed | change |
|---|---|---|---|
| `20261006-235214` | 0/1 | gpt-4o-mini never took the argument-less `caller_identified` edge; it looped on `lookup_patient` | folded the patient lookup into `identify_visit`; every task message names the edge to call |
| `20261006-235435-hand-mini` | 17/25 | (a) `capability:"general"`, `provider_id:"Dr. Nguyen"` and `location_id:"Richmond"` silently returned empty sets, so the agent told callers "we don't do flu shots anywhere"; (b) repeated identical lookups; (c) the wrong type id at booking; (d) "not accepting new patients" read as a refusal for an existing patient | argument validation with name→id resolution when unambiguous; a repeat guard in the builder; a booking mismatch that says which field is wrong; patient-aware provider results (`can_see_this_patient`) |
| same | — | **bug:** pgvector returns a `Vector` object on cache hits, and the exception was swallowed by the degradation path, so the vector ranker was silently off for any repeated phrase | decode fixed; degradation is now logged |
| `20261006-235916-hand-mini-2` | 24/25 | content-free queries got confident matches | `unsure` verdict calibrated on measured similarities; aliases removed from the full-text document (they have their own ranker) |
| `20261007-000113-chen` | 4/4 | — | — |
| stage 1 | tools 85–92% | children booked into adult visits; providers hidden by filters | age-aware type search; "exists but filtered" |
| stage 4 | given: targeted cases 5/8→8/8 | proximity-tie and near-duplicate cases still model-dependent | see § Stage 4 |

## Grading-policy decisions made after seeing results

The judge is self-authored, so every change to it is listed here. Each change was checked against a transcript in which a human would agree with the new verdict.

1. **Confirmation.**
   - Loosened: the caller simulator sometimes tags "Yes, thank you. Goodbye." as `goodbye`, so an un-negated yes also counts as confirmation (`YES_RE`).
   - Then tightened after review: a negated yes ("Absolutely not") does not count, and the booking must be preceded by a read-back naming the doctor or place and the booked time.
2. **Refusal followed by an alternative.** When the request can't be met, a valid alternative the caller accepts counts as *acceptable*, but only if the reason was stated before the booking.
3. **Fairness to the baseline.** The baseline's catalog lives in its prompt, so "named before any lookup returned it" counts the agent's own instructions as seen. To keep that check meaningful for both systems, a new invariant was added: a provider offered with a time at a site where they don't work.
4. **Required questions.**
   - Met by a tool returning `ask` followed, before booking, by an agent question naming two of the returned choices. With exactly two choices, a yes/no question about one of them counts ("Did your doctor ask you not to eat beforehand?").
   - Systems without lookup tools meet the same standard through the candidate names.
5. **Refusal reasons are any-of.** If a request fails for several true reasons (no Dr. Chen here *and* the referral expired), stating any one of them passes. "far" is required only when no clinic at all is near the caller's place.
6. **Typography.** Curly apostrophes are normalised. GPT-5.6 writes "doesn’t", and every negation pattern had been missing it.
7. **Statements inside questions.** "Dr. Sato isn't accepting new patients; would you like another dermatologist?"
   states the fact. A clause counts as a question only if it ends in "?" *and* opens like one ("Do you…", "Which…").
   A read-back phrased "Your appointment is scheduled with… Is that correct?" is still a confirmation question, not
   a false claim of booking. An audit of the flips this caused across 1,222 conversations found: 108 false
   "claimed a booking" fails, introduced and then reverted; 10 not-accepting-new statements now recognised; 5 read-backs
   saying "noon"; 1 not-offered statement.
8. **Same person, two records.** Three personas match people already in the given catalog's registry (same name and
   date of birth). Booking under that existing record is correct, and was wrongly flagged as "wrong patient" (27 conversations in stage 4).

**Independent review.** An opus reviewer was asked to find false passes in the gate. It found 14 issues, including:
- booking a slot other than the one read back;
- "you're all set" with nothing booked;
- questions counted as statements;
- substring time matching;
- no check of the booked patient's identity.

All were fixed in `be7b2f8`, each with a regression test in `backend/tests/test_judge.py`. The leak tripwire now checks oracle values, not just key names. After the gate changes, every run was re-judged from its saved transcripts (`python -m evals.rejudge`). The tables above are the re-judged numbers.

**The LLM quality judge does not separate the systems:** 4.77 for tools vs 4.65 for the baseline, while the gate separates them by 30–50 points. In stage 1 it averaged 4.57/5 on the 90 conversations that broke a safety invariant (5/5 on "recovery" for 72 of them, a mean of 4.5 or more for 67), against 4.78 for those that passed. That is the measured reason it is not the gate.

## Stage 4: after the fixes (`20261007-010011-stage4-given-after`, `-variants-after`, `20261007-010058-ablation-novectors`)

The same given-catalog matrix as stage 1 (tools agent only), after age-aware search, "exists but filtered", and the
judge fixes. All runs on this page are judged by the same final judge.

| model | stage 1 | stage 4 | invariant breaks (stage 4) | $/conv |
|---|---|---|---|---|
| gpt-4o | 92% | 95% | 0 | $0.050 |
| gpt-4o-mini | 86% | 80% | 0 | $0.004 |
| gpt-5.6-luna | 93% | 93% | 1 | $0.006 |
| gpt-5.6-terra | 95% | 88% | 0 | $0.056 |

**What the fixes targeted**, pass rate across all 4 models × 2 (stage 1 → stage 4):
- maria-garcia-pediatrics (child's checkup): 5/8 → 8/8
- spanish-pediatrician-sick-kid: 5/8 → 8/8
- dental-cleaning-near-sunset: 6/8 → 8/8

**What moved the other way** is within noise at N=2: the proximity-tie and gen-surname cases. The remaining hard case,
gen-tie-1, has the caller say "follow-up consultation" (an exact type name). Most models still book "Follow-up Visit",
its registered near-duplicate. The aggregate change per model is −7 to +3 points on 74 conversations, which is noise at
this N; the targeted cases are the signal.

**Variants after the fixes:** gen-collisions 83% / 86%, adv-referral-maze 66% / 77%, adv-near-dupes 60% / 71%
(gpt-4o-mini / gpt-5.6-luna). This run used regenerated personas: the cluster goals no longer quote the tool's hint
text. That removes an advantage the stage-2 personas gave the agent, so the two runs are not directly comparable.
adv-near-dupes stays the weakest catalog: near-identical names with no data to separate them are a meaning problem
that only the lexicon (aliases + registered clusters) fixes, and it was deliberately not tuned for that catalog.

**End-to-end vector ablation** (gpt-4o-mini, given, N=2): **76%** without pgvector vs 86% / 80% with it (stages 1
and 4). The direction matches the offline benchmark (29 vs 22 right of 40), but at this N the end-to-end difference
is suggestive, not conclusive. The given personas mostly use phrases the curated alias table already covers, and
that coverage is exactly why aliases come first. Vectors earn their place on phrasing nobody curated,
which the offline benchmark isolates.

## The voice pipeline still works (`make smoke-pipeline`)

`backend/evals/pipeline_smoke.py` builds the same pipeline as `bot.run_bot`: `OpenAILLMService`,
`LLMContextAggregatorPair`, `PipelineWorker`, `pipecat_flows.FlowManager` and `AgentBuilder` over the Postgres tools. It
replaces only WebRTC, STT and TTS: the caller simulator's words are injected as user turns, and the LLM's text frames
are collected where TTS would read them. The same judge as the text eval then grades the call (rule-based for the
results below; the LLM judge since 2026-10-13).

On the agent's configured model (gpt-4o), all three calls pass:

| persona | verdict | nodes walked | tools called |
|---|---|---|---|
| happy-flu-shot | PASS ideal | greeting → identify_visit → find_slot → confirm_booking → goodbye | lookup_patient, search_appointment_types, check_availability, book_appointment |
| chen-echo-downtown | PASS ideal | same | + find_providers (two Dr. Chens asked about) |
| skin-check-one-spot | PASS ideal | same | cluster question asked, Skin Cancer Screening booked |

With `SMOKE_MODEL=gpt-4o-mini`, the flu shot passes and the Dr. Chen case fails the same way it does in the text eval:
mini lingers in identify_visit instead of taking the edge. That makes the text runner and the real FlowManager
agree on behaviour, not only on plumbing.

`bot.py` also boots against Postgres on a spare port and serves the six new tools at `/api/tools` (the
baseline-only `list_slots` is hidden). The UI's tool picker and the call trace render tools by name, so they need no
change.

## Experiment: give the agent the SQL schema instead of tools (`20261007-072023-sql-experiment`)

Question: instead of six purpose-built tools, what if the agent gets the database schema and writes its own SQL?

> The SQL agent was removed after this experiment (`evals/sql_agent.py`, the `run_sql` tool and the `agent_sql`
> role are gone). To reproduce, check out a commit before the removal. The findings below are why it was removed.

**Setup** (`evals/sql_agent.py`, `--systems tools,sql`).
- The prompt holds the documented schema (13 tables, what the columns mean), the six booking policies, and the
  calendar conventions the tools normally hide: the slot grid, the 30-minute lead and the 21-day horizon. It carries no
  catalog rows and none of the helpers (`bookable`, `bookable_for`, the slot query).
- Tools: `lookup_patient`, `run_sql`, `book_appointment`.
  - `run_sql` runs one query over per-call temp views that are already filtered to this catalog and call, as role
    `agent_sql`, which has no rights on the real tables. The transaction is read-only, with a 3 s timeout and 50 rows
    at most. Writes, `patients`, `public.*` and other calls' bookings are all refused (checked by hand).
  - `book_appointment` still re-checks policies 1–5 and the slot. Only the "offered by check_availability" check is
    off, since there is no check_availability.
- Given catalog, 20 hand personas, N=2, no LLM judge. The tools agent ran in the same run.

| | Pass | Invariant breaks | Tool calls / conv | Prompt tokens / conv | Cost / conv |
|---|---|---|---|---|---|
| tools, gpt-4o | **100%** | 0 / 50 | 3.8 | 23k | $0.047 |
| tools, gpt-4o-mini | **92%** | 0 / 50 | 5.2 | 33k | $0.004 |
| SQL, gpt-4o | 36% | 8 / 50 | 8.2 | 37k | $0.064 |
| SQL, gpt-4o-mini | 20% | 11 / 50 | 14.3 | 79k | $0.007 |

**Why it fails.** The model writes plausible SQL that answers a slightly different question.
- **Lookups that silently return nothing.** 272 of 833 queries came back empty and 93 errored. For example,
  `appointment_types.name = 'flu shot'` is case-sensitive and returns 0 rows, so the agent told an existing patient the
  clinic doesn't offer flu shots. The same query also applied the new-patient filter to an existing patient.
  This is the failure `_validate` exists to prevent in the tools design (an unknown value never becomes an empty set).
- **Slot arithmetic.** 136 booking attempts were rejected at commit. Every rejected time was checked against the
  reference slot query, and none was actually free: 65 were outside the provider's working block, 57 overlapped an
  existing booking, 11 were off the grid and 3 were inside the lead time. Free time is schedules × days × a duration
  grid minus overlaps with bookings at any site. The model rarely gets that right, even with the rule spelled out.
  "No booking made" is the most common failure.
- **Ambiguity is skipped.** The `ambiguity_clusters` table is in the schema, but the agent seldom consults it before
  picking, so "skin check" and "Dr. Chen" cases fail `must_ask`.

**What held:** no unsafe booking was committed. The commit gate in `book_appointment` refused every policy break, so
the cost of raw SQL is accuracy, turns and money, not data integrity. Without that gate, the 7 "provider can't do X
there" and 2 referral rejections would have been bad bookings.

**Conclusion.** Letting the model write queries moves the hard, deterministic parts (eligibility joins, slot
arithmetic, name matching, ambiguity) back onto the model, which is the thing this design was built to avoid. It costs
2–3× the tool calls, more tokens, and 56–72 points of pass rate. The schema is not the right interface for the model;
questions with fixed answers are. Small sample (N=2, one catalog), but the gap is far outside run-to-run noise
(stage 1 varied by a few points).

## Policies as one function each (`20261011-181219-policyset`)

`store/policies.py` now has one function per policy, and every tool runs a `PolicySet`
(2026-10-11). Same eval as the SQL experiment's tools rows (given catalog, 20 personas, N=2):

| | Before | After |
|---|---|---|
| gpt-4o | 100%, 3.8 tool calls, $0.047 | 100%, 3.9 tool calls, $0.052 |
| gpt-4o-mini | 92%, 5.2 tool calls, $0.004 | 96%, 4.8 tool calls, $0.004 |

No regression. The one wrong booking (mini on `spanish-pediatrician-sick-kid`) has failed on mini in about half its
runs since stage 1. Refusals now name the policy (`"policies": ["6"]`), and the 300-provider catalog's full
policy pass takes 11 ms.

## The judge is now an LLM (`judge-agreement-20261013-192414`)

**What changed.** The rule-based judge (regexes over the transcript plus `grade_booking`) is gone. One LLM call per
conversation (`evals/judge.py`) now decides everything: invariants, outcome tier, required questions and statements,
information answers, and the 1–5 quality scores. The oracle is still code. It is the ground truth for what this
caller should get in this catalog, and the judge gets it as an **answer key in names** (visit types by tier, the
provider asked for, the bookable options, the nearest locations, the soonest slot, what must be asked or stated).
The judge also gets the whole call in order (caller and assistant lines, every tool call and result) and the
bookings actually written to the database, with the policy check of each. `pass` is still the same fixed combination
(no violation, no failure, tier at least acceptable).

**Which model judges.** A one-off script (`evals/judge_agreement.py`, since deleted; in git at `4c539aa`) re-judged a stratified sample of saved transcripts: 150
conversations, 50 per system (tools, baseline, SQL), half of them old failures. It compares against the verdicts saved
by the rule-based judge.

| judge | pass/fail agreement | tier agreement | old violations it also caught | cost (150 convs) |
|---|---|---|---|---|
| gpt-4o (first rubric) | 69% | 71% | 9 / 19 | $0.76 |
| gpt-5.6-terra (first rubric) | 84% | 95% | 9 / 19 | $1.36 |
| **gpt-5.6-terra (final rubric)** | **87%** | **93%** | **18 / 19** | **$1.36** |

gpt-4o also passed 26 conversations the rules had failed, and it is one of the models under test, so it would grade
itself. The default is `gpt-5.6-terra` (`--judge-model` / `JUDGE=` to change it). That is about $0.009 per
conversation, roughly a fifth of what a gpt-4o agent conversation costs.

**Who was right when they disagreed.** Every disagreement was checked by hand against its transcript.
- Final rubric, 20 disagreements: **the LLM was right in 17**, wrong in 1 and unclear in 2. The one violation it
  "missed" was itself a false alarm from the rules ("2:50" read as 2:50 am).
- What the rules missed and the LLM caught:
  - the agent read back one location and booked another (twice: Gary Park vs Cedar Hill, Richmond vs Maple Grove;
    the rules only matched the surname);
  - the agent offered provider/location pairs the booking check later refused;
  - "Yes, we do eye exams!" (the clinic doesn't), passed by the rules because a later "don't have any available"
    matched the "not offered" pattern;
  - times invented by the SQL agent, passed by the rules because the SQL prompt's own example slot grid
    ("09:00, 09:45, 10:30, 11:15") counted as a source;
  - bookings straight after the caller picked a time, with no read-back and no yes (the rules trusted the
    simulator's own "confirm" label).
- What the LLM got wrong under the first rubric, now fixed in the rubric:
  - it treated a caller picking a time as consent;
  - it counted a *rejected* booking attempt as "the booking", so a reason stated after it seemed late;
  - it didn't accept "we have several Dr. Nguyens, which one?" as asking;
  - it added requirements from the caller's goal that weren't in the key;
  - it worked out its own "soonest slot" when the key gave none (the 1 remaining error).

**Acceptance cases.** The rule-based judge's 11 test conversations (every false pass and false fail found in review)
all pass on terra. One is loose: about one run in three, the judge folds "you're all set" into the made-up
confirmation-code finding instead of listing it separately. The verdict is a fail either way. (Those tests were
removed along with the rest of `backend/tests` in `b508754`.)

**Costs of the switch.**
- A verdict is now a judgement. Re-judging the same transcript can occasionally flip a borderline case, and
  `make smoke-pipeline`'s exit code is probabilistic.
- Each conversation costs one judge call more than before, because quality used to be a separate, optional call
  (`--no-llm-judge` is gone).
- The pass rates above were produced by the rule-based judge and have not been re-judged. The re-judge script was
  removed in a later cleanup (`git show 4a4011f:backend/evals/rejudge.py` restores it). Re-judging costs about
  $0.009 per conversation, so all ~1,500 saved conversations would cost about $14.

## Limits of this evidence

- **No audio.** STT errors are simulated only by persona wording ("Ngyuen", "Gary") and the fuzzy and phonetic matchers. The full Pipecat pipeline was exercised with text in place of audio (above). The WebRTC and ElevenLabs legs are unchanged by this work and were not re-run with a microphone.
- **A single simulator model** (gpt-4o) plays every caller. A different simulator would shift absolute numbers. Comparisons across agent models and systems are fair because it is held fixed.
- **Small N.** N=2 on the given catalog and N=1 on the variants. A case at 1/2 is a finding, not a rate. The per-case tables in each run's summary.md show the distribution.
- **Self-authored data.** The generated catalogs reuse the given catalog's vocabulary plus 40 synthetic types.
  Each bundle layers its own `lexicon.json` over the shared base: hand-authored for gen-large and adv-near-dupes,
  and for all of them aliases derived mechanically from their own type names ("EKG / Electrocardiogram" → "ekg").
  The derived aliases were added after stages 1–4 ran, so those runs used base + hand-authored lexicons only.
  The lexicon was not tuned per variant from failures; that is deliberate, since the variants test transfer.
- **Grading tolerance.** "Soonest" is graded with 2 days of tolerance for acceptable. Ties in proximity accept either location.
