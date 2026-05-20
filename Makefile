# =============================================================================
# Labor Law Partner backend — Makefile
# =============================================================================

.PHONY: help install dev migrate makemigrations seed test lint format \
        shell up down logs build push clean tier load-corpus

PYTHON ?= python
DC = docker compose
ECR_URL ?= $(shell terraform -chdir=infra/terraform output -raw ecr_repository_url 2>/dev/null)
CLUSTER ?= llp-production-cluster

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install Python dependencies locally (no Docker)
	pip install -r requirements.txt

# ── Local dev with docker-compose ─────────────────────────────────────────

up:  ## Start docker-compose stack (web, db, redis, worker, beat)
	$(DC) up --build -d

down:  ## Stop docker-compose stack
	$(DC) down

logs:  ## Tail docker-compose logs (web service)
	$(DC) logs -f web

dev:  ## Run Django dev server (assumes db+redis are already up)
	$(PYTHON) manage.py runserver 0.0.0.0:8000

# ── Database / Django management ──────────────────────────────────────────

migrate:  ## Apply migrations
	$(DC) exec web $(PYTHON) manage.py migrate

makemigrations:  ## Generate new migrations from model changes
	$(DC) exec web $(PYTHON) manage.py makemigrations

seed:  ## Seed default tier configs (free_guest, free_subscribed, mini, max)
	$(DC) exec web $(PYTHON) manage.py seed_tiers

shell:  ## Open a Django shell in a running container
	$(DC) exec web $(PYTHON) manage.py shell

createsuperuser:  ## Create an admin user
	$(DC) exec web $(PYTHON) manage.py createsuperuser

load-corpus:  ## Load corpus zip — requires CORPUS_ZIP=path
	@if [ -z "$(CORPUS_ZIP)" ]; then echo "Set CORPUS_ZIP=/path/to/llp-chat-data6.zip"; exit 1; fi
	$(DC) cp "$(CORPUS_ZIP)" web:/tmp/corpus.zip
	$(DC) exec web $(PYTHON) manage.py load_corpus --zip /tmp/corpus.zip

# ── Quality ──────────────────────────────────────────────────────────────

test:  ## Run pytest in the web container
	$(DC) exec web pytest

test-fast:  ## Run only unit tests (no DB)
	$(DC) exec web pytest -m "not integration"

lint:  ## Run ruff
	$(DC) exec web ruff check apps config

format:  ## Auto-format with ruff
	$(DC) exec web ruff format apps config

typecheck:  ## Run mypy
	$(DC) exec web mypy apps config

# ── Image build / push ───────────────────────────────────────────────────

build:  ## Build the production Docker image locally
	docker build -t llp-backend:latest .

push:  ## Push to ECR (requires ECR_URL or terraform state)
	@if [ -z "$(ECR_URL)" ]; then echo "Set ECR_URL or run from a directory with terraform state"; exit 1; fi
	aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin $(ECR_URL)
	docker tag llp-backend:latest $(ECR_URL):latest
	docker push $(ECR_URL):latest

deploy:  ## Push image and force ECS rolling deploy
	$(MAKE) build
	$(MAKE) push
	aws ecs update-service --cluster $(CLUSTER) --service llp-production-web --force-new-deployment
	aws ecs update-service --cluster $(CLUSTER) --service llp-production-worker --force-new-deployment

# ── Misc ─────────────────────────────────────────────────────────────────

clean:  ## Remove Python caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf staticfiles/ build/ dist/ *.egg-info/

reset-db:  ## Drop and recreate the local DB (DATA LOSS!)
	$(DC) down -v
	$(DC) up -d db redis
	sleep 5
	$(DC) up -d web worker beat
	sleep 10
	$(MAKE) migrate seed

.DEFAULT_GOAL := help
