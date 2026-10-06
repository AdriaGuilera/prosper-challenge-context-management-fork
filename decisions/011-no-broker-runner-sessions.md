# Decision 011 — No queue and no worker pool: the runner's one-pipeline-per-session is the concurrency model

**Status:** accepted
**Date:** 2026-10-06
**Related:** 010 (ship the spine), `reviews/round2-informed-critique.md`

## Context

The proposal was a queue in front of cloned agent workers so that two demo users can talk at once,
plus Langfuse for tracing. Four options were laid out: (1) nothing, (2) registry + rules in one
process, (3) process per session, (4) queue + worker pool for calls.

## Decision

**Option 1.** No broker, no worker pool, no supervisor, no per-session process and no proxy.
Concurrency is the Pipecat dev runner instantiating `bot(runner_args)` **once per connection**, with
`run_bot` constructing the transport-bound pipeline, services, LLM context and FlowManager fresh each
time.

This is already the architecture of `backend/bot.py`: no session state lives in a module global, and
every per-call object is built inside `run_bot`. The decision is therefore to **keep it** and to
explicitly not build anything in front of it.

## Why not a queue

- A call is stateful, ordered and **non-resumable** — the pipeline holds audio buffers, STT partials,
  VAD state, the LLM context and the current node position. Queue semantics (retry, redelivery) are
  actively wrong for audio: at-least-once redelivery would replay the caller's speech.
- A queue in front of calls is really *admission control*, and admission control only matters at
  capacity. Two demo users never reach capacity.
- It costs hours that decision 010 just recovered, and it puts new failure modes on the demo path.

## Accepted risks — named, not mitigated

1. A blocking call anywhere in a turn path stalls **every** session in the process. The mitigation is
   a coding rule — no synchronous I/O in the turn path — not infrastructure.
2. One crash ends all in-flight calls. Acceptable for a demo; not acceptable for production.

## Deliberately not built

Broker, worker pool, supervisor, port assignment, media proxy in front of the transport, and a session
registry subsystem.

## Where a queue does belong

Offline **eval fan-out** (N persona cases × M workers — retryable, resumable, latency-tolerant) and
fire-and-forget side effects (confirmation, audit flush). Neither sits on the call path.

## Tracing — Langfuse

Agreed in principle. Pipecat ships built-in OpenTelemetry with a **documented Langfuse exporter** and
an existing conversation → turn → {stt, llm, tts} hierarchy, so this is configuration rather than
hand-wiring an LLM SDK. Emit the resolver, validity, alias and ambiguity decisions as explicit spans
so that the trace *is* the visible evidence of the Phase 2 context management.

- **Self-hosting is ruled out on resources.** Langfuse v3 minimums are web 4 GiB + worker 4 GiB +
  Postgres 4 GiB + ClickHouse 8 GiB + Redis 1.5 GiB — five containers and roughly 16–24 GiB RAM.
  This box has 7.6 GB and runs live apps. It would also contradict 010.
- **Therefore Langfuse Cloud Hobby:** free, 50k units/month, 30-day retention, 2 seats, hard cap
  (no overage — ingestion simply stops), ~1k ingest req/min.
- **Never in the turn path:** batched async export, drop-on-failure, no per-turn flush. Otherwise a
  tracing outage turns into voice latency, which is the one budget we cannot spend.
- **Unverified:** the tracing API version. Pipecat's docs list `enable_tracing` as deprecated since
  1.8 in favour of `FrameProcessorSetup.setup()`, while `backend/requirements.txt` pins
  `pipecat-ai>=1.4.0,<2`. Pin and check against the installed version before wiring it.

## Open

Whether the dev runner runs each session as a task inside one process or as its own process. Pipecat
is not installed on this box (`backend/.venv` absent), so this could not be confirmed from source.
It does not change the decision — no queue either way — but it does change how the guarantee is
worded.

## What would reverse this

Real concurrent load, calls that must survive each other's crashes, or a reviewer requiring process
isolation. Then option 3, process-per-session — which brings port assignment or a media proxy with it.
