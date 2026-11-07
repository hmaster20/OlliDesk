.PHONY: all setup install download-monaco download-mermaid download-slm \
        lint test check clean check-fast typecheck check-strict

VENV_NAME ?= .venv
VENV_BIN := $(VENV_NAME)/Scripts
PYTHON := $(VENV_BIN)/python
PIP := $(VENV_BIN)/pip

all: check

$(VENV_NAME):
	python -m venv $(VENV_NAME)

install: $(VENV_NAME)
	$(PIP) install -e .[dev]

install-docs: $(VENV_NAME)
	$(PIP) install -e .[docs]

download-monaco:
	$(PYTHON) -m ui.web_editor.download_monaco

download-mermaid:
	$(PYTHON) -m ui.web_editor.download_mermaid

download-slm:
	$(PYTHON) -m agents.download_slm

setup: install download-monaco download-mermaid download-slm

lint: $(VENV_NAME)
	$(VENV_BIN)/ruff check .

test: $(VENV_NAME)
	$(VENV_BIN)/pytest tests/ -q --tb=short

check: check-fast lint test

clean:
	rm -rf $(VENV_NAME)
	rm -rf ui/web_editor/vendor/monaco
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

check-fast: $(VENV_NAME)
	$(VENV_BIN)/ruff check . --select F,E9
	$(PYTHON) -m compileall -q agents core fs state tools ui

typecheck: $(VENV_NAME)
	$(VENV_BIN)/mypy core agents fs state tools || true

check-strict: check-fast lint typecheck test
