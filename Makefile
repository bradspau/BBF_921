# TMF921 Intent Management API — Makefile
# Usage: make <target>

.PHONY: help models models-check install test test-unit test-integration test-contract lint format clean dev seed

PYTHON      := python3
OAS_FILE    := docs/spec/TMF921_Intent_Management_v5.0.0_oas.yaml
GENERATED   := src/api/schemas/generated.py
SCRIPTS_DIR := scripts

help:
	@echo ""
	@echo "  TMF921 Intent Management API"
	@echo ""
	@echo "  Setup"
	@echo "    install          Install all dependencies (pip)"
	@echo ""
	@echo "  Code Generation"
	@echo "    models           Generate + patch Pydantic models from OAS YAML"
	@echo "    models-check     Verify generated models pass all patch tests"
	@echo ""
	@echo "  Development"
	@echo "    dev              Start FastAPI dev server (hot reload)"
	@echo "    seed             Seed the graph store with sample intents"
	@echo ""
	@echo "  Testing"
	@echo "    test             Run all tests with coverage"
	@echo "    test-unit        Run unit tests only"
	@echo "    test-integration Run integration tests only"
	@echo "    test-contract    Run Schemathesis contract tests vs OAS"
	@echo ""
	@echo "  Quality"
	@echo "    lint             Run ruff linter"
	@echo "    format           Run ruff formatter"
	@echo "    clean            Remove __pycache__, .pytest_cache, coverage files"
	@echo ""

# ─── Setup ────────────────────────────────────────────────────────────────────

install:
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

# ─── Code Generation ──────────────────────────────────────────────────────────

models:
	@echo "→ Checking OAS file exists..."
	@test -f $(OAS_FILE) || (echo "ERROR: $(OAS_FILE) not found. Add OAS YAML before running make models." && exit 1)
	@echo "→ Generating Pydantic models from OAS..."
	bash $(SCRIPTS_DIR)/generate_models.sh
	@echo "→ Applying TMF921 patches..."
	$(PYTHON) $(SCRIPTS_DIR)/patch_generated_models.py
	@echo "✓ Models written to $(GENERATED)"

models-check: models
	@echo "→ Running model patch tests..."
	pytest tests/unit/test_generated_models.py -v
	@echo "✓ Models verified"

# ─── Development ──────────────────────────────────────────────────────────────

dev:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

seed:
	$(PYTHON) seed_data/seed_intents.py

# ─── Testing ──────────────────────────────────────────────────────────────────

test:
	pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=80

test-unit:
	pytest tests/unit/ -v --cov=src --cov-report=term-missing

test-integration:
	pytest tests/integration/ -v

test-contract:
	@test -f $(OAS_FILE) || (echo "ERROR: $(OAS_FILE) required for contract tests." && exit 1)
	schemathesis run $(OAS_FILE) --base-url http://localhost:8000/tmf-api/intentManagement/v5 --checks all

# ─── Quality ──────────────────────────────────────────────────────────────────

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .coverage htmlcov/ reports/ 2>/dev/null || true
	@echo "✓ Cleaned"
