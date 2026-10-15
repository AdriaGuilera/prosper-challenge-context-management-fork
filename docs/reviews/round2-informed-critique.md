# Review — round 2: informed critique

**Date:** 2026-10-05
**Method:** the same four models, each given **its own round-1 proposal** plus **all nine decision
records in full**, and asked whether the design is over-built, what is genuinely wrong, what their
own proposal got wrong, what to keep or delete, and to score it against the brief's five criteria.
Verbatim responses in `/tmp/panel2/`. Repo read-only; no retry needed at `max_tokens=16000`.

## Scores

| model | overall | originality/rigour | scalability | trade-offs | **scoping** | demo |
|---|---|---|---|---|---|---|
| `mimo-v2.6-flash` | **6** | 8 | 8 | 9 | **3** | 3 (est.) |
| `glm-5.3-flash` | **7** | 9 | 9 | 9 | **4** | unverifiable |
| `kimi-k3` | **7** | 9 | 9 | 8 | **3** | unscorable |
| `mimo-v2.6-pro` | **7** | 9 | 8 | 9 | **4** | pending, at risk |

**The pattern is unambiguous and consistent:** the analytical work scores 8–9. Pragmatic scoping —
an explicitly scored criterion — scores 3–4. All four named it as the failure, and three pointed out
we had already diagnosed it ourselves in `round1-blind-panel.md` and then did not act on it. In
kimi's words: *"Three exit ramps, none taken. The over-build isn't a failure of analysis — it's a
failure to act on the analysis."*

## The cut list all four independently produced

1. Policy IR + CEL + verification ladder (approach 002 layers 3–6, decision 001)
2. PostGIS + the fictional gazetteer (006, 007) — keep the gazetteer as one JSON fixture, not a subsystem
3. CP-SAT slot selection
4. Multi-tenant namespacing, per-tenant observability, validity windows (parts of 002, 003, 008)
5. **Postgres** — and the reason matters, see below

Note the convergence: this is the same list three of four gave independently in round 1, now with
our decision records in front of them.

## Defects found — these are *wrong*, not scope

1. **Decision 004 contradicts our own policy-6 gate.** The fallback — "pick the safer default, state
   which one was booked, make it changeable" — books from an undiscriminated set, which
   approach 002's commit gate exists to reject. *Fixed.*
2. **Evidence provenance is inconsistent.** Every load-bearing measurement (79 ms ingest, 0.017 ms
   anchored, 760/760) was taken on **SQLite**. Decision 005 then switched the target to Postgres
   without re-measuring. So the measured claims and the accepted stack disagree — *"the crack a
   reviewer will find."* See decision 010.
3. **Decision 009 is too strong as written.** "Never auto-resolved, regardless of any score" would
   make the agent re-ask after the caller has already answered with a disambiguator. The rule must be
   *never on ranker score alone*. *Fixed.*
4. **An inflated number.** Approach 001 says materialised triples become "millions of rows" at target
   scale; at 1,000 providers it is ~30k — off by two orders of magnitude. Notable only because we
   measured everything else. *Fixed.*
5. **One mega-function manufactures a failure we ourselves warn about.** A single `find_bookable`
   with ten optional parameters maximises the Cartesian product of *valid-looking* mistakes — which is
   precisely the "wrong-but-valid parameter set" risk in our own risk section. Narrow tools aligned to
   conversational beats shrink the space per call.
6. **Nothing in the record states what is built versus designed.** If the IR/CEL stack, Postgres and
   the gazetteer are not running, the ADRs misrepresent the submission. That is worse than
   over-build — it is a reality gap a reviewer finds in ten minutes.

## The gap all four flagged

**Phase 1 and the Phase 1 ↔ Phase 2 seam have no design at all.** Nine decisions, two approaches, one
panel review — and nothing on the node-graph editor, the node JSON format, how the resolver is
invoked from a node, how state flows back into the next node's prompt, or what the caller hears when
the system disambiguates mid-call.

Three of four chose this as *"the one thing neither design addresses."* kimi: *"the scheduling agent is
meant to be built in, and exercised through, the node-graph UI… as designed, the builder UI demos a toy
agent while the scheduling agent lives as hand-wired backend."* The brief's arc is *"now that you have a
platform for building agents, design context management"* — the seam **is** the challenge, and we
skipped it. Phase 1 is a hard requirement, the demo runs through it, and it is the highest-variance
part of the deliverable.

## Two sharper hits worth recording

- **The panel is weaker validation than `round1` claimed.** `task.md` itself names *"structured tool
  calls"* as a suggested direction, so convergence is partly prompt-induced. And three of four models
  returned empty output at the first token budget, so the harness was fragile. *"External validation
  of the spine overclaims what is a weak-ish signal dressed in good prose."* Fair.
- **STT noise is not designed for.** Trigram matching catches *typos*, not *mishearings* — on a real
  call "Chen" → "Shen", "Geary" → "Gary", "Sato" → "Otto". Neither our golden set nor the alias table
  handles phonetic substitution. Fix is cheap: metaphone keys on provider and location names, plus one
  eval pass through the real STT.

## What their own proposals got wrong (the control question)

All four answered it substantively, and the most valuable admissions were about things *we* found:
the `specialty`-as-join-key landmine (glm: *"a correctness bug my design was one implementation step
away from"*), the unparseable `hours` string (three of four missed it), and that near-duplicate pairs
for skin and physicals differ on *nothing* stored (mimo-pro: *"my 'the options carry the
differentiating attribute' is fabricated where no attribute exists"*).

## The recommended fix, from all four

Identical advice: **ship the spine, and reclassify the rest as designed-and-descoped**, citing our own
"what would reverse this" clauses — because this challenge satisfies them. kimi: *"that converts the
over-build from a scoping failure into the strongest scoping evidence in the submission — and it's a
week of build they don't have to do."* See decision 010.
