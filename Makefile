.PHONY: bootstrap-dev test lint typecheck check

PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip

ifneq ($(shell command -v uv 2>/dev/null),)
BOOTSTRAP_DEV = uv sync --extra dev
RUN = uv run
else
BOOTSTRAP_DEV = test -x $(VENV_PYTHON) || $(PYTHON) -m venv $(VENV); $(VENV_PIP) install -q -e .[dev]
RUN = $(VENV_PYTHON) -m
endif

bootstrap-dev:
	@$(BOOTSTRAP_DEV)

test:
	@$(BOOTSTRAP_DEV)
	$(RUN) pytest -q

lint:
	@$(BOOTSTRAP_DEV)
	$(RUN) ruff check .

typecheck:
	@$(BOOTSTRAP_DEV)
	$(RUN) mypy --strict src

check: lint typecheck test
