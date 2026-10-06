# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + Vite + React Flow for the builder UI. The existing Python backend (Pipecat voice pipeline, `AgentBuilder`) stays as is and serves the agent API and the WebRTC test call.

## Users

Primary: Prosper engineers reviewing the AI Software Engineer Challenge, watching a live end-to-end demo. They judge engineering judgment — above all the Phase 2 context-management approach — so the UI must make the system's internals legible while a call is happening, not just look like a product.

## Product Purpose

A voice-agent builder for healthcare appointment scheduling. A user edits an agent as a graph of nodes (each a conversation step) and edges (transitions), then places a browser test call against it. The agent being demoed is a clinic scheduler that navigates a large, messy catalog of locations, providers and appointment types without dumping it into the LLM context.

Success: a reviewer can watch a call and see why the agent picked a given appointment type, provider and location — which tools ran, what they returned, which ids were resolved, and when the agent asked a disambiguating question instead of guessing.

## Positioning

The LLM proposes; deterministic code disposes. Retrieval is narrow, typed tool calls over a normalised SQLite catalog (anchored queries, bounded results that carry their discriminators), node-scoped prompts and tools in the flow graph, and a commit-time `bookable` check so an invalid booking cannot be written. Ambiguity the data cannot resolve is asked in the caller's vocabulary.

## Operating Context

- Reviewed during a live demo call; also read later via `solution.md` and the `decisions/` record.
- Voice path: browser mic → ElevenLabs STT → OpenAI LLM → ElevenLabs TTS → browser.
- Agents are declarative JSON (`backend/example_flow.json`) compiled by `backend/agent_builder/` into a Pipecat Flows graph.
- Time budget for the whole challenge: roughly 8–12 hours.

## Capabilities and Constraints

In scope for the UI:
- **Graph editor** — edit nodes, edges, prompts and per-node tools; save the agent JSON.
- **Test-call panel** — place a browser call, live transcript, and a live view of tool calls, results and resolved ids.

Out of scope for now (explicitly not chosen): a catalog manager UI and a cost/accuracy comparison view.

Constraints:
- No synchronous I/O in the call's turn path (decision 011).
- Availability is mocked; the catalog is `backend/data/catalog.json` (82 appointment types, 50 providers, 8 locations).
- Policy IR/CEL, PostGIS, CP-SAT, embeddings and multi-tenancy are designed but descoped (decision 010).

## Evidence on Hand

- `task.md` — the challenge brief.
- `decisions/001–012`, `approaches/`, `reviews/` — the design record.
- Measured numbers in the record: 760/760 valid triples via the `bookable` view, anchored queries at 0.017 ms, full-catalog dump ≈8k tokens now.
- No customers, testimonials or production usage exist; do not fabricate any.

## Product Principles

1. Show the mechanism. Every tool call, resolved id and disambiguation the agent makes should be visible during the call.
2. The demo path is sacred. Nothing on the live-call path may be fragile or slow.
3. Scope is a signal. Build the graph editor and the call panel well; leave the rest to `solution.md`.
4. Never let the UI imply the LLM decided something the code enforced, or the reverse.
