.PHONY: help install install-dev lint format test clean build \
       frontend-install frontend-dev frontend-build

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the package in development mode
	pip install -e .

install-dev: ## Install development dependencies
	pip install -e .[dev]
	pre-commit install

lint: ## Run linting tools
	black --check src/ tests/
	isort --check-only src/ tests/
	flake8 src/ tests/
	mypy src/

format: ## Format code
	black src/ tests/
	isort src/ tests/

test: ## Run tests
	pytest tests/ -v --cov=src/api --cov-report=html --cov-report=term-missing

clean: ## Clean build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/

build: ## Build the package
	python -m build

run-api: ## Run the API server
	cd src && python -m uvicorn api.api:app --host 0.0.0.0 --port 8000 --reload

run-worker: ## Run the worker
	cd src && python -m api.worker

run-hydra: ## Run the Hydra client (usage: make run-hydra ARGS="case_number")
	cd src && python -m api.hydra_client $(ARGS)

frontend-install: ## Install frontend dependencies
	cd frontend && npm install

frontend-dev: ## Run frontend dev server
	cd frontend && npm run dev

frontend-build: ## Build frontend for production
	cd frontend && npm run build

image-api: ## Build Docker image for api
	podman build -t intelliaide-api:latest -f src/api/Dockerfile src/api/

image-frontend: ## Build Docker image for frontend
	podman build -t intelliaide-frontend:latest frontend/

image-run-api: ## Run API Docker container
	podman run -p 8000:8000 intelliaide-api:latest