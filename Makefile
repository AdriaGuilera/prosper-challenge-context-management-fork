# Prosper Challenge — run everything from the repo root.

VENV := backend/.venv
PYTHON := $(VENV)/bin/python

.PHONY: help install run dev build clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv, install Python and frontend dependencies
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r backend/requirements.txt
	cd frontend && npm install

build: ## Build the builder UI into frontend/dist (served by `make run`)
	cd frontend && npm run build

run: build ## Build the UI and run the backend (then open http://localhost:7860)
	cd backend && ../$(PYTHON) bot.py

dev: ## Backend + Vite dev server with hot reload (open http://localhost:5173)
	@trap 'kill 0' EXIT; (cd backend && ../$(PYTHON) bot.py) & (cd frontend && npm run dev)

clean: ## Remove the venv and Python caches
	rm -rf $(VENV) frontend/node_modules frontend/dist
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
