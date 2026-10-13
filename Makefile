# Prosper Challenge — run everything from the repo root.

VENV := backend/.venv
PYTHON := $(VENV)/bin/python

.PHONY: help install run dev build clean db-up db-down db-reset ingest eval eval-smoke smoke-pipeline

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv, install Python and frontend dependencies
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r backend/requirements.txt
	cd frontend && npm install

build: ## Build the builder UI into frontend/dist (served by `make run`)
	cd frontend && npm run build

run: build ## Build the UI and run the backend (then open http://localhost:7860)
	cd backend && ../$(PYTHON) bot.py

# ---- store (Postgres + pgvector + PostGIS on localhost:5433) ----

db-up: ## Start Postgres in Docker and wait until it accepts connections
	docker compose up -d --build db
	@until docker compose exec -T db pg_isready -U prosper >/dev/null 2>&1; do sleep 1; done

db-down: ## Stop Postgres (data kept in the pgdata volume)
	docker compose down

db-reset: ## Drop all data, restart Postgres and re-ingest every catalog
	docker compose down -v
	$(MAKE) db-up
	$(MAKE) ingest CATALOG=all

CATALOG ?= given
ANCHOR ?=
ingest: ## Ingest catalogs into Postgres (CATALOG=given|all|<name>, ANCHOR=today for the live demo)
	cd backend && ../$(PYTHON) -m store.ingest $(if $(filter all,$(CATALOG)),--all,$(CATALOG)) $(if $(ANCHOR),--anchor $(ANCHOR),)

# ---- eval (text only) ----

MODELS ?= gpt-4o-mini
N ?= 3
CASES ?=
comma := ,
eval: ## Run the eval: CATALOG=given|all|<name> MODELS=gpt-4o,gpt-4o-mini N=3 CASES=<glob> BASELINE=1 JUDGE=<judge model>
	cd backend && ../$(PYTHON) -m evals.run --catalog $(CATALOG) --models $(MODELS) --n $(N) $(if $(CASES),--cases '$(CASES)',) $(if $(BASELINE),--systems tools$(comma)baseline,) $(if $(JUDGE),--judge-model $(JUDGE),)

smoke-pipeline: ## The real Pipecat pipeline (FlowManager + LLM + tools), text in place of audio
	cd backend && ../$(PYTHON) -m evals.pipeline_smoke

eval-smoke: ## Cheap end-to-end check: given catalog, one model, N=1
	$(MAKE) eval CATALOG=given MODELS=gpt-4o-mini N=1

dev: ## Backend + Vite dev server with hot reload (open http://localhost:5173)
	@trap 'kill 0' EXIT; (cd backend && ../$(PYTHON) bot.py) & (cd frontend && npm run dev)

clean: ## Remove the venv and Python caches
	rm -rf $(VENV) frontend/node_modules frontend/dist
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
