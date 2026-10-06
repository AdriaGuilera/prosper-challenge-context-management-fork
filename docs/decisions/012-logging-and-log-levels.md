# Decision 012 — Logging: stdout JSON as primary, a rotating file behind a named volume, levels as the dial

**Status:** accepted
**Date:** 2026-10-06
**Related:** 011 (Langfuse), `reviews/round2-informed-critique.md`

## Context

The requirement: really good logging into a log file, working identically when hosted in Dokploy and
when developed locally. Additionally, Claude Code must be able to read the logs and the traces —
the Langfuse CLI (`npx @langfuse/cli api <resource> <action>`) and the Langfuse MCP server both cover
that. And: log everything, because this is a demo. Levels are the operator's dial, not the
implementer's choice.

Two facts about the current repo: there is **no logging configuration at all** (no `logger.add`
anywhere — loguru's default stderr sink and four `logger.info` calls), and there is **no Dockerfile or
compose file**, so "hosted in Dokploy" also means writing the container definition.

## Decision

**1. stdout is the primary sink; the file is secondary.**
Verified against Dokploy's source: its log viewer runs
`docker container logs --timestamps --tail N --since X --follow` per container and filters with a plain
`grep -iF`. Anything not on stdout/stderr is invisible there. Therefore **one log record per line**,
JSON, so a record stays atomic and greppable, and never multi-line (an exception traceback is the one
sanctioned exception, emitted as a single `exc` field).

**2. The file sink lives behind a Docker named volume.**
Dokploy's AutoDeploy performs a `git clone` on every deployment, which wipes the repo directory — so a
bind mount from the repo is emptied on redeploy. Named volumes also the only ones eligible for
Dokploy's Volume Backups. Locally the same path is just a directory.

**3. loguru, already a dependency.**

- `rotation="10 MB"`, `retention=10`, `compression="gz"`, and `enqueue=True`, `catch=True`
- `enqueue=True` is **not cosmetic**: it moves the write off the event loop, which is the mitigation for
  accepted risk 1 in decision 011 — a blocking write in a turn stalls every session in the process
- `catch=True` so a failing sink can never raise into the pipeline
- if the log dir is not writable (container without the volume), log a warning to stdout and continue —
  logging must never crash the agent

**4. Format per environment, from one event.**

- console: pretty and colourised locally, `serialize=True` (JSON lines) when deployed
- file: always JSON lines, so it is parseable by `jq`, grep, and an agent
- switched on an explicit `APP_ENV`, never sniffed

**5. One correlation id across logs and traces.**
A `session_id` on every record via `logger.contextualize`, set when the pipeline starts, and it **must
be the same value as the Langfuse trace id**. With N sessions in one process (decision 011), this is
what keeps two concurrent calls separable and what lets a bad trace lead to its log lines.

**6. Log everything.**
Full utterances and full tool payloads, no redaction — the catalog addresses are provably fabricated
and every caller is a synthetic persona, so no real patient data exists anywhere in the system. The
redaction switch stays in the code so the default can be inverted, but for this deliverable the default
is full. One exclusion that is absolute: **API keys are never logged**, including in the startup config
dump.

**7. Levels are the dial.**
The event catalogue is fixed; the level assigned to each event is a one-word change. Level contract:

- **ERROR** — something failed and a caller was affected: tool raised, DB error, provider error,
  commit-gate rejection, session died. Always with exception and stack.
- **WARNING** — degraded but recovered, or a near-miss worth counting: truncation occurred, ambiguity
  fell back to the default, unmatched phrase, idempotent retry hit, latency over budget, an unbookable
  type offered and caught.
- **INFO** — the narrative of a normal call, per the catalogue below.
- **DEBUG** — frame-level events and full-context dumps. Firehose; local only.

## The INFO catalogue

| event | fields |
|---|---|
| **STT** | transcript, confidence, endpointing latency |
| **TTS** | utterance text, time-to-first-audio |
| **Queries** | resolved query name, parameters, rows, latency, truncation flag, `EXPLAIN QUERY PLAN` |
| **Latency** | per stage: VAD end → STT → resolver → LLM → first audio |
| **Resolutions** | caller phrase → candidate ids → score/gap → ambiguity outcome → question asked |
| **Writes** | catalog version, idempotency key, confirmed vs attempted |
| **Lifecycle** | session start/end with duration, and the resolved config dump (no secrets) |

`EXPLAIN QUERY PLAN` costs nothing — it compiles the statement without executing it — and it is the
literal evidence for the scalability claim: the line reads `USING INDEX pat` or `SCAN pat`, which is
what round 2 asked us to evidence instead of assert.

## Consequence for spans

Because Claude Code reads **traces** as well as logs, the decision data must also live in span
**attributes**, not only in the log file: resolved ids, candidate set, gap, ambiguity outcome, question
asked, tool parameters and row counts. Logs carry the reasoning in full; spans carry the same decisions
next to their timing. If a decision exists only in a log line, the trace shows latency with no
explanation, and neither artefact is readable alone.

## Consequences

- the container definition must declare the named volume and set `APP_ENV`
- every event goes through one helper, so the level is genuinely a one-word change
- log volume at INFO is a few hundred lines per call — no sampling needed at demo scale

## What would reverse this

Real patient data, which turns redaction on by default rather than off. Or trace volume over the Hobby
plan's 50k units — at which point sampling, not redaction, is the lever.
