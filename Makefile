COMPOSE := docker compose
AUDIMETRO_HOST ?= gcp-apps

# Sello de build para /version (commit corto + fecha UTC)
BUILD_INFO ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)$(shell git diff --quiet HEAD 2>/dev/null || echo -dirty)-$(shell date -u +%Y-%m-%d)

.DEFAULT_GOAL := help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

vendor-core:  ## Refresh the vendored radar-core snapshot (private lib)
	rsync -a --delete \
		--exclude '.git' --exclude '.venv' --exclude 'tests' \
		--exclude 'uv.lock' --exclude '__pycache__' --exclude '.*_cache' \
		--exclude '*.md' \
		../radar-core/ vendor/radar-core/

install: vendor-core  ## Sync deps locally
	uv sync

dev:  ## Run the daemon locally (foreground)
	uv run python -m audimetro

tick:  ## Collect stats and send the digest NOW (ignores hour/sent guards)
	uv run audimetro tick

test:  ## Run tests
	uv run pytest -q

lint:  ## Lint + typecheck
	uv run ruff check . && uv run mypy audimetro

build: vendor-core  ## Build the image
	BUILD_INFO=$(BUILD_INFO) $(COMPOSE) build

up:  ## Start the daemon (detached)
	$(COMPOSE) up -d

down:  ## Stop
	$(COMPOSE) down

logs:  ## Follow logs
	$(COMPOSE) logs -f audimetro

deploy: vendor-core  ## rsync + rebuild + up on AUDIMETRO_HOST
	rsync -az --delete --exclude '.git' --exclude '.env' --exclude '.venv' ./ $(AUDIMETRO_HOST):~/audimetro/
	ssh $(AUDIMETRO_HOST) 'cd ~/audimetro && BUILD_INFO=$(BUILD_INFO) docker compose up -d --build'
