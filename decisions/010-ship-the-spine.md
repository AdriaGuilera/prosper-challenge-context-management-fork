# Decision 010 — Ship the spine; reclassify the rest as designed-and-descoped

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** the scope implied by decisions 001, 006, 007, parts of 003 and 008, and the Postgres
target of 005. Revises nothing in 002, 004, 009.

## Context

Two rounds of external review by four models, the second informed (see `reviews/`). Scores: 6, 7, 7, 7
overall, with **pragmatic scoping at 3, 4, 3, 4** — an explicitly scored criterion — and unanimous
agreement that the record describes a platform where the brief grades a solution. All four also
converged on the same cut list, and on the same remedy.

## Decision

**1. Ship these.** Normalised store with edge indexes; the `bookable` relation; commit-time
revalidation; the SQL-vs-Python differential test (760/760); alias table + registered ambiguity
clusters; decision 004's caller-vocabulary disambiguation; bounded, answer-shaped results with no
silent truncation; aggregates for coverage questions; ingest lint (orphan and unbookable types); hours
parsed at ingest; mocked availability in the dropped `schedules` shape, **joined last**; idempotency
key plus pinned catalog version on the write; generated ground truth, minimal pairs, and the
over-asking metric.

**2. Ship SQLite.** Every load-bearing measurement — 79 ms ingest, 0.017 ms anchored, 760/760 — was
taken on SQLite. Postgres was accepted in 005 and never re-measured, so the evidence and the stack
disagree. Postgres becomes a migration paragraph in `solution.md`, not a running dependency.
The gazetteer survives as **one JSON fixture** (it is what makes distance cases gradeable), not as a
subsystem.

**3. Design-and-descope: present in `solution.md`, do not build.** Policy IR + CEL + the verification
ladder; PostGIS; CP-SAT slot selection; per-source identity namespacing, per-tenant observability, and
the structural/operational validity windows; embeddings (already 009).

The mechanism that makes this credible rather than convenient: **each of those already carries its own
"what would reverse this" clause, and this challenge satisfies the clause.** Decision 001 says
single-tenant with a handful of rules means SQL alone is fine; 005's reversal condition is a local-only
constraint; 002 concedes multi-tenancy is not in the brief. Citing our own reversal conditions is the
scoping evidence, and it is stronger than never having designed them.

**4. The highest-priority gap — and it is not scope.** Phase 1 and the Phase 1 ↔ Phase 2 seam have no
design at all: the node graph, the node JSON format, how a node invokes the resolver
(`pre_action` + `{{ state }}`), how resolved ids flow into the next node's prompt, how the builder UI
renders tool calls and resolved ids during a test call, and what the caller hears mid-disambiguation.
All four reviewers named this, three of them as *the* single most important shared omission. Phase 1 is
a hard requirement and the live demo runs through it. Closing it outranks polishing anything in §3.

## Consequences

- the build surface drops to something that fits an 8–12 hour budget
- `solution.md` carries a scoping section that is evidence rather than apology
- two decisions are now historical design work rather than implementation instructions — they stay in
  the repo, which is the point of the record
- the demo gets the hours that §3 would have consumed

## What would reverse this

A multi-hospital deployment with schema variance, or a brief that scored platform design. Then 001,
005, 006, 007 and 008 come back off the shelf — which is exactly why they were written down.
