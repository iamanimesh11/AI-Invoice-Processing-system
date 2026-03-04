.PHONY: help up down build logs ps test lint format secrets seed shell-db shell-airflow trigger-dag

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── Docker ────────────────────────────────────────────────────────────────────
up: ## Start all services (detached)
	docker compose up -d --build

down: ## Stop and remove containers (keep volumes)
	docker compose down

destroy: ## Stop containers AND delete volumes (wipes DB + data)
	docker compose down -v

build: ## Rebuild all images without cache
	docker compose build --no-cache

logs: ## Tail logs from all services
	docker compose logs -f

ps: ## Show running service status
	docker compose ps

# ── Development ───────────────────────────────────────────────────────────────
up-dev: ## Start with dev overrides (hot reload, debug ports)
	docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --build

# ── Testing ───────────────────────────────────────────────────────────────────
test: ## Run unit and integration tests
	docker compose exec upload_service pytest tests/ -v

test-unit: ## Run unit tests only
	docker compose exec upload_service pytest tests/unit/ -v

lint: ## Run ruff linter
	docker compose exec upload_service ruff check .

format: ## Auto-format with ruff
	docker compose exec upload_service ruff format .

# ── Database ──────────────────────────────────────────────────────────────────
migrate: ## Run Alembic migrations
	docker compose exec airflow_scheduler alembic upgrade head

shell-db: ## Open psql shell
	docker compose exec postgres psql -U $$POSTGRES_USER -d $$POSTGRES_DB

# ── Airflow ───────────────────────────────────────────────────────────────────
shell-airflow: ## Open bash shell in scheduler container
	docker compose exec airflow_scheduler bash

trigger-dag: ## Manually trigger the invoice pipeline DAG
	docker compose exec airflow_scheduler airflow dags trigger invoice_processing_pipeline

dag-list: ## List all registered DAGs
	docker compose exec airflow_scheduler airflow dags list

# ── Utilities ─────────────────────────────────────────────────────────────────
secrets: ## Generate Fernet key and web secret and print them
	@bash scripts/generate_secrets.sh

seed: ## Seed demo invoice data into the database
	docker compose exec upload_service python scripts/seed_demo_data.py

upload-sample: ## Upload the sample invoice PDF via the API
	curl -s -X POST http://localhost:8000/api/v1/upload-invoice \
		-F "file=@scripts/sample_invoice.pdf" | python3 -m json.tool
