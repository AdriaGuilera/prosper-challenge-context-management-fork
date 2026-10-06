# Prosper Challenge — Context Management

Voice AI for healthcare scheduling. An agent is a **graph of nodes** (Pipecat Flows), defined declaratively as JSON and compiled into a runnable voice pipeline.

- **Phase 1** — a UI to edit the node graph and place a test call.
- **Phase 2** — a context-management approach so a scheduling agent can reliably and cost-effectively navigate a large catalog of locations, doctors, and appointment types.

```
browser mic  ->  ElevenLabs STT  ->  OpenAI LLM  ->  ElevenLabs TTS  ->  browser
```

The **agent builder** (`frontend/`, React + React Flow) edits the node graph and places a test call in the same workspace: during the call the graph lights up along the path the conversation takes, and a side panel streams the transcript and every catalog tool call.

## Quickstart

Requires **Python 3.11+** and **Node 20+**. Run from the repo root:

```bash
make install
make run
```

Open `http://localhost:7860`, pick an agent, press **Test call**, allow mic access, and talk to it. `Ctrl+C` to stop. For frontend work, `make dev` runs the backend plus the Vite dev server on `http://localhost:5173`. (`make help` lists all targets.) The original prebuilt client is still at `/client`.\
\
Remember to update the `.env` file accordingly.

## Layout

| Path | Responsibility |
| --- | --- |
| `backend/bot.py` | The voice pipeline (WebRTC + ElevenLabs STT/TTS + OpenAI LLM). Runs the agent named in the call's `/start` body, streams graph events to the UI over RTVI, and serves the built UI and the agents API. No graph logic lives here. |
| `backend/agent_builder/` | All agent-building code. `schema.py` = the declarative `AgentConfig` / `Node` / `Edge` contract (plus per-node `tools` and editor-only `ui`); `builder.py` = `AgentBuilder`, which validates the JSON, compiles it into a Pipecat Flows graph and emits call events; `tools.py` = the catalog lookups a node can expose. |
| `backend/agents_api.py` | List / load / save agent JSON for the UI. A save is rejected unless `AgentBuilder` can compile it. |
| `backend/agents/` | Agents **as data**. `clinic-scheduler.json` uses the catalog tools; `example-flow.json` is the original sample. |
| `frontend/` | The builder UI: graph editor, inspector, and live test-call panel. |
| `docs/` | The design record: `task.md`, `decisions/`, `approaches/`, `reviews/`, `eval/`, and `PRODUCT.md`. |
| `backend/data/catalog.json` | A deliberately large, deliberately messy clinic catalog (locations, providers, appointment types, booking rules) for the Phase 2 work. See [`backend/data/README.md`](backend/data/README.md). |

Agents are plain JSON files in `backend/agents/`; the UI creates and edits them, and `DEFAULT_AGENT` in `bot.py` picks the one the prebuilt `/client` runs.