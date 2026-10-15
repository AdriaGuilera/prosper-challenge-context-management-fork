# Test personas + oracle — eval harness design

**Status:** draft for discussion
**Phase:** 2 (context management) — the harness that proves cost *and* accuracy
**Depends on:** `approaches/001-normalized-store-and-validators.md` (the thing being tested)

---

## 0. Vocabulary (decide this first, or ground truth will leak)

Three words, kept strictly apart:

| word | means | who sees it |
|---|---|---|
| **case** | one test: persona + oracle + run config, in one file | the harness only |
| **persona** | the simulated caller — in-fiction self-facts + goal + behaviour | the simulator LLM |
| **oracle** | the ground truth of what a correct outcome looks like | the judge only, **never** the simulator |

The naming discipline is load-bearing. The moment "persona" means "the JSON with the answer
in it", someone serialises the whole file into the simulator's prompt and the caller starts
volunteering the booking.

## 1. The information barrier

**The rule:** a persona knows things about *itself*. It never knows anything about the
clinic's inventory.

- Legitimate persona knowledge: "I'm María García, I'm a new patient, I don't have a
  referral, my knee has been sore for two weeks, I'd rather not drive far, I saw Dr. Chen
  last year."
- Forbidden persona knowledge: any `prov_*` / `loc_*` / `appt_*` id, any type or location
  name the clinic would have to look up, and of course the expected booking.

The distinction is exactly *what a real caller would know walking in*. A real caller does
not know that Dr. Chen works at four sites or that an echocardiogram needs imaging — which is
why the agent has to do work, and why the test means anything.

### Enforcement, not good intentions

1. **One accessor.** `loader.load_for_simulator(case) -> dict` is the *only* function that
   builds the simulator payload. Nothing else touches a case file.
2. **Leak tripwire test.** `test_no_oracle_leak`: for every case file, assert the serialised
   simulator payload contains no oracle key name and no substring matching
   `prov_|loc_|appt_|sched_`, and does not contain any expected outcome value. Mechanical,
   cheap, runs in CI.
3. **Optional runtime assert** on the built system prompt, same regex, before dispatch.
4. **Two-file split** is the paranoid alternative (`001-foo.persona.json` +
   `001-foo.oracle.json`). Safer byte-wise, worse ergonomically — see open decision 1.

## 2. Case file shape

```jsonc
{
  "case_id": "006-referral-required-absent",
  "description": "New-ish caller wants a procedure that requires a referral they don't have",
  "tags": ["gate:referral", "needs:mocked-patient-state"],
  "run_config": {
    "catalog_sha": "…",           // pin the data the case was written against
    "availability_seed": 4207,    // deterministic mocked slots, per case
    "max_turns": 14,
    "simulator": { "model": "…", "temperature": 0.3, "prompt_hash": "…" }
  },

  "persona": {
    "identity": {
      "full_name": "Alan Reyes",
      "is_new_patient": false,
      "referral_on_file": null,        // never mention it; the caller doesn't know it's missing
      "preferred_language": "English"
    },
    "goal": {
      "intent": "book",
      "complaint": "I need an echocardiogram, my cardiologist mentioned it ages ago",
      "constraints": {
        "prefer_provider_name": "Dr. Chen",
        "must_be_soon": true
      },
      "unknowns": ["doesn't know an echo needs imaging or a referral"]
    },
    "behaviour": {
      "cooperativeness": 0.6,
      "verbosity": "medium",
      "vagueness": "low",
      "reveal_policy": "mentions the provider only if asked who she'd prefer",
      "on_disambiguation_question": "answers helpfully",
      "on_implausible_offer": "pushes back once, then accepts",
      "patience": "3 failed attempts"
    },
    "speech_realism": { "names_spelled_out": [], "mispronunciations": [] },
    "stop_conditions": ["booking confirmed", "agent ends call", "caller gives up after patience"]
  },

  "oracle": {
    "outcome_kinds_allowed": ["booking", "refusal", "information"],
    "conditional_expectations": [
      { "when": "caller accepts a different provider",
        "ideal": [ { "type_id": "appt_021", "location_id": ["loc_001","loc_004"] } ],
        "acceptable": [ { "type_id": "appt_021", "location_id": ["loc_001","loc_004"],
                          "provider_id": ["prov_046"] } ] },
      { "when": "caller insists on Dr. Chen",
        "ideal": [ { "provider_id": "prov_000", "type_id": "appt_021",
                     "location_id": ["loc_001","loc_004"] } ] }
    ],
    "must_ask": ["is_the_caller_new", "which_of_these_providers"],
    "must_not_offer": ["any type with required_capability at a location lacking it"],
    "must_state": ["that a referral on file is needed before this can be booked"],
    "invariants": ["no invalid (provider, location, type) triple is ever offered",
                   "nothing is booked without explicit caller confirmation",
                   "no invented location, type or time is ever spoken"],
    "budget": { "max_llm_calls": 12, "max_input_tokens": 20000, "max_ms_per_turn": 2500 }
  }
}
```

## 3. Ground truth is a *set* of graded outcomes, not a booking

The single most important modelling decision. A single `expected: {…}` is wrong for most
interesting cases:

- **Ambiguous caller** — "I'd like to see Dr. Nguyen" (8 matches, all individually valid). A
  correct *conversation* asks who they mean; a correct *booking* is whichever one the caller
  then chose. Grading on one id is meaningless.
- **Vague complaint** — a sore knee could defensibly be Orthopedics, Physical Therapy (which
  is undeliverable in this catalog — see below), or a generic Family Medicine visit. More
  than one answer is right.
- **Better-than-expected** — an agent that notices the referral is missing and explains it
  *before* offering slots has done better than one that just books. Single-value grading
  can't see that.

So: **tiers**.

| tier | score | means |
|---|---|---|
| `ideal` | 1.0 | the best defensible outcome |
| `acceptable` | 0.7 | a correct, slightly less good outcome |
| `suboptimal` | 0.3 | not wrong, but a worse choice than available |
| `wrong` | 0.0 | a violated invariant, or an outcome in the forbidden set |

### Conditional expectations

Because the simulator is nondeterministic, the expected outcome *depends on how the
conversation branched*. "If the caller specified Pulmonology, then provider ∈ {prov_023,
prov_024, prov_028}". The oracle is therefore a **set of rules keyed on the persona's revealed
facts**, not a flat expectation — which is only checkable if the harness logs what the
simulator actually said. Hence the event log (§6).

### Invariants are global, not per-case

Some properties must hold in *every* case, independent of goal, and are cheap to check
mechanically:

- no invalid (provider, location, type) triple is ever offered or booked
- an appointment type with no provider behind it is never offered
- capability-gated types are only offered at locations that carry the capability
- the referral and new-patient gates are never bypassed
- nothing is booked without explicit confirmation
- no invented entity or time is spoken

These are the highest-value assertions in the whole harness, because they're deterministic
and they're exactly where a naive full-context design fails.

## 4. Behaviour: orthogonal to goal

Behaviour must be **separate from goal**, so the same goal can be crossed with different
behaviours. This is what makes ~20 files cover a real matrix instead of a single happy path.

Reusable profiles in `eval/personas/behaviors/`, crossed with goals:

- `vague-cooperative` — under-specifies everything, answers questions honestly
- `terse-impatient` — gives one word at a time, gets terse after two failed attempts
- `chatty-rambler` — buries the reason in a story; tests turn-taking and focus
- `adversarial` — volunteers wrong details, changes its mind mid-call
- `stt-hostile` — mispronounced names, names spelled out letter by letter, hesitations
- `correction-hunter` — pushes back whenever the offer is implausible

**Cooperativeness must default low, not high.** A cooperative caller flatters the system: it
volunteers its provider, its new-patient status and its location up front, so the agent never
has to ask a good question. Real callers don't. The default case should make the agent work.

Ground truth stays per-behaviour-neutral: the *behaviour* changes the path, the *oracle*
judges the endpoint and the required questions.

## 5. Harness tiers

Two tiers, because they trade against each other:

**Tier 1 — text mode (volume).** Drive the agent's LLM + Flows graph directly with text; no
audio. Fast, cheap, mostly deterministic, CI-friendly. This is where the accuracy and cost
numbers come from, run over hundreds of cases.

**Tier 2 — audio mode (realism).** The persona is itself a voice agent: persona TTS →
agent STT → agent → agent TTS → persona STT. Tests the parts text mode can't see — name
mangling through STT, barge-in, false endpointing, latency under real audio. Slow and
expensive, so a small suite (the STT-hostile and interruption cases only).

**Tier 2 warning.** Two voice agents talking to each other produce turn-taking pathologies
that have nothing to do with the agent under test: both start speaking, both wait, one
interrupts or they deadlock on silence. Budget for an explicit turn-taking policy and VAD
tuning, and don't read a Tier 2 failure as a scheduling-agent bug until you've ruled out
the harness itself.

**Bonus signal, specific to this repo:** the agent is a node graph, so the harness can assert
on *graph* behaviour — which nodes were visited, whether a disambiguation node was ever
entered, whether the conversation re-entered a previous node. That's a stronger signal than
reading the transcript, and it ties the eval to the Phase 1 builder UI.

## 6. Event log

Judge the log, not just the transcript. Per turn, record:

- speaker, utterance text, timestamp, per-turn wall clock
- every resolver / tool call: name, arguments, row count, latency
- every booking write, with the resolved ids
- tokens in/out, LLM calls, time-to-first-audio
- node transitions (which graph node handled the turn)

The deterministic judge needs the tool-call and booking events. Judging only the transcript
can't tell the difference between an agent that queried well and one that guessed right.

## 7. Judging: two judges, one gate

**Deterministic judge — the pass/fail gate.** Pure code against the event log and the oracle:
final booking state ∈ the conditional expectation set; no invariant violated; no forbidden
outcome; budget respected. No LLM anywhere in the correctness path. If correctness depends on
an LLM judge, the eval is as flaky as the thing it's testing.

**LLM judge — quality only, after the gate.** A rubric: did it ask a clear question, was it
concise enough for voice, did it explain the referral requirement plainly, did it recover
gracefully when corrected. Scored separately, never allowed to overturn the gate.

**Blind comparisons.** When comparing two designs, the judge must not know which system
produced which transcript — shuffle and strip identifiers, or A/B preference flips into
brand preference.

## 8. Determinism and repeatability

- Availability is **mocked and seeded per case**, or "soonest available" is ungradeable.
- Pin `catalog_sha`, simulator model + version, temperature, prompt hash in `run_config`, so
  a result is comparable across weeks.
- Simulator LLM ⇒ nondeterminism. Run each case N times (5–10) and report the *distribution*,
  not one lucky pass. A case that passes 3/5 is a finding, not a pass.
- Record transcripts + logs in `eval/runs/` (gitignored); commit the cases, never the runs.

## 9. Coverage matrix — derived from the catalog's own traps

Every hard case below is grounded in real data, so there's no invention required:

| # | case | tests | ground truth shape |
|---|---|---|---|
| 001 | new patient books a plain type | happy path | booking, 1 ideal |
| 002 | "I'd like to see Dr. Nguyen" | surname collision (8 matches) | must ask, then conditional |
| 003 | "Dr. Maria Garcia" | exact full-name collision (Pediatrics MD vs Family Medicine NP) | must ask, then conditional |
| 004 | "Dr. Chen for an echocardiogram" | provider + capability + multi-location | ideal = loc_001 \| loc_004 |
| 005 | physical vs wellness visit | near-duplicate types | must clarify, both acceptable |
| 006 | echo, no referral on file | referral gate | refusal + explanation |
| 007 | "do you have an eye doctor?" | unbookable type (appt_045…) | honest "we can't" |
| 008 | new patient, only provider not accepting new | new-patient gate | refusal, alternative offered |
| 009 | "my knee has been sore" | no symptom vocabulary in the data | meaning layer; graded set |
| 010 | soonest vs preferred provider | conflicting preferences | must surface the trade-off |
| 011 | agent books the wrong Dr. Chen | correction handling | accept push-back, fix it |
| 012 | "cancel Tuesday, rebook Friday" | multi-intent | both actions, correct order |
| 013 | name that matches nobody | near-miss handling | ask, never invent |
| 014 | "what time do you close?" | information only | no booking |
| 015 | mispronounced surname | STT robustness (Tier 2) | must ask, not guess |

Plus the same goals crossed with the behaviour profiles from §4.

## 10. Reporting

Per case: outcome tier, gate pass/fail, which invariant broke, turns, LLM calls, input/out
tokens, latency percentiles, and the transcript.

Aggregate: **accuracy and cost reported together, never one without the other** — a design
that scores 95% accuracy at 8k tokens/turn has not beaten one at 90% and 500, and the brief
explicitly grades how we reason about that trade-off. The naive full-context baseline runs
through the *same* harness so the comparison is apples-to-apples.

## 11. Proposed layout

```
eval/
  persona-design.md            # this document
  schema/case.schema.json      # JSON Schema, validated in CI
  cases/001-… 015-….json
  personas/behaviors/*.json    # reusable behaviour profiles
  harness/loader.py            # the ONLY path case -> simulator prompt
  harness/test_no_oracle_leak.py
  harness/run_text.py          # tier 1
  harness/run_audio.py         # tier 2
  harness/judge_deterministic.py
  harness/judge_llm.py
  runs/                        # transcripts, logs, reports (gitignored)
```

## 12. Open decisions

1. **One file with a loader-enforced split, or two files per case?** (lean: one file + leak
   test — the ergonomics of editing persona and oracle side by side matter when writing 40 cases)
2. **Graded tiers, or stricter pass/fail?** (lean: graded — half these cases are ungradeable
   as a single expected value)
3. **Do we build Tier 2 (audio) at all?** (lean: yes but small — the STT-hostile cases are
   exactly where a "correct" text-mode agent can fail in reality)
4. **How many repeats per case?** (lean: 5, reporting the distribution)
5. **Who writes the first 5 cases?** The catalog traps are known; this is cheap now and it's
   the artefact that makes every later claim about accuracy testable.
