.PHONY: all setup install download-monaco lint test check clean

VENV_NAME ?= .venv
VENV_BIN := $(VENV_NAME)/Scripts
PYTHON := $(VENV_BIN)/python
PIP := $(VENV_BIN)/pip

all: check

$(VENV_NAME):
	python -m venv $(VENV_NAME)

install: $(VENV_NAME)
	$(PIP) install -e .[dev]

download-monaco:
	$(PYTHON) -m ui.web_editor.download_monaco

setup: install download-monaco

lint: $(VENV_NAME)
	$(VENV_BIN)/ruff check .

test: $(VENV_NAME)
	$(VENV_BIN)/pytest tests/ -q --tb=short

check: lint test

clean:
	rm -rf $(VENV_NAME)
	rm -rf ui/web_editor/vendor/monaco
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
