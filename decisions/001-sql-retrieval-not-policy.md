# Decision 001 — SQL is the retrieval layer, not the policy artifact

**Status:** accepted
**Date:** 2026-10-05
**Supersedes:** nothing · **Superseded by:** —

## Context

All six booking policies are expressible in SQL, and this was verified: the `bookable`
view reproduces every valid (provider, location, type) triple exactly — 760/760 over 797
raw combinations. So SQL is *capable* of the policy slot.

The question was whether it should *hold* the policies, given a scenario where each
hospital brings its own JSON and its own prose rules.

## Decision

SQL owns retrieval, structural constraints, aggregates, availability joins and tenant
scoping. Policy is held in a structured IR with CEL as the compiled, checkable form.
SQL views for the structural subset are permitted as an **optimisation**, licensed by the
differential test — never as the source of truth.

## Rationale

The scaling axis is **clients × rules × change rate** — an operations problem, not a data
problem. SQL scales on rows and loses on humans:

1. **Authoring** — SQL policies are per-tenant handwork. N hospitals × M rules is N×M
   engineering tasks, permanently.
2. **Review** — a clinic ops person cannot read a correlated subquery with a semi-join.
   They can confirm three positive and three negative examples. SQL fails the review
   gate, which in healthcare is the gate that matters.
3. **Change** — a CEL/IR rule change is one readable line in a diff. A SQL predicate
   change is an opaque rewrite of a nested SELECT, and its diff tells a reviewer nothing.
4. **Divergence** — per-tenant SQL forks have no structure to reason about, so
   cross-tenant questions ("which tenants reject new patients for referral-required
   types?") are unanswerable. A structured IR makes them a query.
5. **Portability** — date/time arithmetic is the most dialect-divergent corner of SQL,
   and date/time is exactly what these policies use (12-month referral windows, `now`).
   Generated SQL binds the rule set to one engine.
6. **Injection and silent wrongness** — storing rules as SQL means executing generated
   SQL. To do it safely you must parse and allowlist it, which *is* the check phase CEL
   provides for free.

## Consequences

- one retrieval layer instead of N; tuned once, measured (0.017 ms anchored)
- an IR and an admin surface must be built: the source of truth, plus examples
- CEL becomes the runtime evaluator (or a compiled form of it), not the stored form
- absence semantics, the decision algebra, versioning and audit are ours to build
  regardless of engine
- structural SQL views remain available as an optimisation

## What would reverse this

A single-tenant deployment with a handful of hand-written rules. With one hospital and
six rules, SQL alone is genuinely fine — and already proven. The gap opens only when
rules are *generated* per tenant, which is the multi-hospital scenario.
