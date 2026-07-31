.PHONY: test lint typecheck check

PYTHON ?= python3
RUN := $(if $(shell command -v uv 2>/dev/null),uv run,$(PYTHON) -m)

test:
	$(RUN) pytest -q

lint:
	$(RUN) ruff check .

typecheck:
	$(RUN) mypy --strict src

check: lint typecheck test
