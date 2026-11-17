# Euron TokenProfiler: developer entry points. `make` lists every target.
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

COMPOSE_DIR := deploy/compose
FLOCI       := docker compose -f $(COMPOSE_DIR)/compose.yaml -f $(COMPOSE_DIR)/compose.floci.yaml
VENV        := .venv
PY          := $(VENV)/bin/python
TF_IMAGE    := hashicorp/terraform:1.16.6
TF_ENVS     := local-floci aws-dev aws-prod

.PHONY: help install env up down logs ps key demo e2e e2e-chaos e2e-browser screenshots test test-console \
        lint typecheck build tf-fmt tf-validate openapi dev clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\n"} /^[a-zA-Z0-9_-]+:.*##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Create the virtualenv and install Python and console dependencies
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -e packages/core -e apps/api -e apps/mock-llm -e packages/sdk \
	  pytest pytest-asyncio httpx ruff mypy boto3 pyyaml pillow tiktoken
	corepack enable && pnpm install

dev: ## Run the API on this machine at http://localhost:8380 (data in .localdata/)
	TP_DATA_DIR=$(CURDIR)/.localdata TP_PORT=8380 TP_CORS_ORIGINS=http://localhost:3300 $(VENV)/bin/tokenprofiler serve

env: ## Generate deploy/compose/.env with random local-only secrets
	@scripts/gen-local-env.sh

up: env ## Start the full stack on emulated AWS (Floci): Terraform, API, mock LLM provider, console
	$(FLOCI) up -d --build
	@echo "console http://localhost:3300   api http://localhost:8380/docs   mock-llm http://localhost:8390   floci http://localhost:4866"

down: ## Stop the stack and delete its volumes (and the local Terraform state, which belongs to the emulator)
	-$(FLOCI) down -v --remove-orphans
	@rm -f infra/terraform/envs/local-floci/terraform.tfstate infra/terraform/envs/local-floci/terraform.tfstate.backup

logs: ## Tail logs (SERVICE=api|console|mock-llm|floci|terraform)
	$(FLOCI) logs -f --tail 100 $(SERVICE)

ps: ## Show stack status
	$(FLOCI) ps

key: ## Create a new all-scopes API key (printed once); APPS=a,b limits it to those apps
	@$(FLOCI) exec -T api tokenprofiler keys:create --name cli --scopes '*' $(if $(APPS),--apps $(APPS),)

demo: ## Load 45 days of synthetic traffic, budgets and proxied calls into the running stack
	@$(PY) scripts/demo/seed.py

e2e: ## Full-stack end-to-end assertions against the running stack
	@$(PY) scripts/demo/e2e.py

e2e-chaos: ## Adversarial e2e: load, hostile input, restarts, a dead provider and S3, tenant isolation
	@$(PY) scripts/demo/e2e_chaos.py

e2e-browser: ## Chromium browser QA with Playwright against the running stack
	pnpm --filter @tokenprofiler/e2e exec playwright install chromium
	pnpm e2e

screenshots: ## Regenerate README screenshots and the tour GIF (stack must be up and seeded)
	pnpm --filter @tokenprofiler/e2e exec playwright install chromium
	pnpm --filter @tokenprofiler/e2e screenshots
	$(PY) scripts/demo/make_tour_gif.py

test: ## Python unit and integration tests (core, SDK, API, mock provider)
	$(PY) -m pytest packages/core packages/sdk apps/api apps/mock-llm -m "not network"

test-console: ## Console unit tests
	pnpm console:test

lint: ## Ruff and ESLint
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	pnpm console:lint

typecheck: ## mypy (strict) and tsc
	$(VENV)/bin/mypy packages/core packages/sdk apps/api apps/mock-llm
	pnpm console:typecheck

build: ## Build the console and the Docker images
	pnpm console:build
	docker compose -f $(COMPOSE_DIR)/compose.yaml build

openapi: ## Regenerate docs/api/openapi.yaml from the FastAPI app
	$(PY) scripts/gen_openapi.py

tf-fmt: ## Format Terraform
	docker run --rm -v $(CURDIR)/infra/terraform:/infra -w /infra $(TF_IMAGE) fmt -recursive

tf-validate: ## terraform validate for every environment (no credentials needed)
	@for e in $(TF_ENVS); do echo "== $$e"; \
	  docker run --rm --entrypoint sh -e TF_PLUGIN_CACHE_DIR=/plugin-cache -v tp-tfplugins:/plugin-cache -v $(CURDIR)/infra/terraform:/infra -w /infra/envs/$$e $(TF_IMAGE) \
	    -c "terraform init -backend=false -input=false -no-color >/dev/null && terraform validate -no-color" || exit 1; done

clean: ## Remove build output and caches
	rm -rf apps/console/.next apps/console/node_modules/.cache .pytest_cache .ruff_cache .mypy_cache .localdata
