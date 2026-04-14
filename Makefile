.PHONY: help up down logs migrate api worker beat consumer test lint

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Infrastructure ──────────────────────────────────────────

up: ## Start all infrastructure (Postgres, RabbitMQ, Kafka)
	docker-compose up -d postgres rabbitmq zookeeper kafka

down: ## Stop all containers
	docker-compose down

up-all: ## Start everything including app services
	docker-compose up -d --build

logs: ## Tail logs from all containers
	docker-compose logs -f

# ── Database ────────────────────────────────────────────────

migrate: ## Run Alembic migrations
	alembic upgrade head

migrate-new: ## Create new migration (usage: make migrate-new msg="add users table")
	alembic revision --autogenerate -m "$(msg)"

# ── Application ─────────────────────────────────────────────

api: ## Run FastAPI dev server
	uvicorn src.app:app --reload --port 8000

worker: ## Run Celery worker
	celery -A src.worker.celery_app worker -l info -c 2

beat: ## Run Celery Beat (scheduler)
	celery -A src.worker.celery_app beat -l info

consumer: ## Run Kafka consumer
	python -m src.kafka.consumer

# ── Testing ─────────────────────────────────────────────────

test: ## Run all tests (requires Docker for testcontainers)
	pytest -v

test-fast: ## Run tests excluding slow integration tests
	pytest -v -m "not slow"

test-cov: ## Run tests with coverage report
	pytest -v --cov=src --cov-report=term-missing

# ── Quality ─────────────────────────────────────────────────

lint: ## Run linters (ruff)
	ruff check src/ tests/

format: ## Auto-format code (ruff)
	ruff format src/ tests/

# ── Utilities ───────────────────────────────────────────────

shell: ## Open Python shell with app context
	python -c "from src.database import SessionLocal; db = SessionLocal(); print('DB session ready: db')" -i

clean: ## Remove __pycache__ and .pyc files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.pyc" -delete 2>/dev/null; true
