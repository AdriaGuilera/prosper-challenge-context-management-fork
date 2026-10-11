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
make db-up                  # Postgres 17 + pgvector + PostGIS in Docker, on localhost:5433
make ingest ANCHOR=today    # load the catalog; the mocked calendar starts this week
make run
```

Docker is required for the store. `make db-reset` wipes it and re-ingests every catalog under `backend/catalogs/`.

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

## The store and the eval (Phase 2)

| Path | Responsibility |
| --- | --- |
| `backend/store/` | The Postgres store ([decision 013](docs/decisions/013-postgres-pgvector-postgis-built.md)): `schema.sql` (normalised tables; the `bookable` relation, a second implementation of policies 1-5 for the differential test), `ingest.py` + `lint.py`, `policies.py` (one function per booking policy, and the `PolicySet` every tool runs them through), `calendar.py` + `slots.py` (seeded calendar, SQL slots), `search.py` (alias + full text + pgvector, fused), `geo.py` (gazetteer + KNN). |
| `backend/catalogs/` | Catalog bundles: the payload plus what we derive for it (gazetteer mapping, lexicon, patient registry, calendar seed). `given/` points at the untouched `data/catalog.json`; `gen-*` and `adv-*` are variants and hand-built adversarial catalogs (`python -m evals.catalog_gen`). `gazetteer.json` is the one fictional map. |
| `backend/evals/` | The text-mode eval: runner over the real node graph, caller simulator, per-catalog oracle, deterministic judge (the gate) and LLM quality judge, scenario generators, naive baseline, report. |
| `eval/personas/`, `eval/behaviors/` | Hand-written personas (catalog-independent) and reusable behaviour profiles. Runs land in `eval/runs/` (gitignored). |

```bash
make test                                               # store, policies, slots, tools, leak tripwire
make eval CATALOG=given MODELS=gpt-4o-mini N=3          # one catalog, one model
make eval CATALOG=all MODELS=gpt-4o,gpt-4o-mini,gpt-5.6-luna,gpt-5.6-terra N=3 BASELINE=1
cd backend && .venv/bin/python -m evals.show latest <job substring>   # read a conversation
```

Results and what they taught us: [`docs/eval/results.md`](docs/eval/results.md).

Agents are plain JSON files in `backend/agents/`; the UI creates and edits them, and `DEFAULT_AGENT` in `bot.py` picks the one the prebuilt `/client` runs.