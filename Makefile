.PHONY: bootstrap-dev test test-quick test-bootstrap-contract test-bootstrap-deep test-bootstrap-rootlike lint typecheck check

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

# Fast suite for local iteration and pre-push checks.
# Excludes interactive deep bootstrap scenarios, script-level bootstrap
# contract tests, and heavyweight local bootstrap integration tests.
test-quick:
	@$(BOOTSTRAP_DEV)
	$(RUN) pytest -q -m "not bootstrap_deep and not bootstrap_contract and not bootstrap_slow"

# Script-level bootstrap contract tests with mocked apt/git/uv.
test-bootstrap-contract:
	@$(BOOTSTRAP_DEV)
	$(RUN) pytest -q tests/test_bootstrap_script.py -m "bootstrap_contract"

# Deep bootstrap scenarios closest to real curl|bash + PTY interaction.
# Excludes dedicated log-leakage test by user request to keep this lane focused.
test-bootstrap-deep:
	@$(BOOTSTRAP_DEV)
	$(RUN) pytest -q tests/test_bootstrap_pty.py -m "bootstrap_deep" -k "not leak"

# Optional root-like run inside a container. Useful for local verification
# without modifying host system state. Requires podman.
test-bootstrap-rootlike:
	@if command -v podman >/dev/null 2>&1; then \
		podman run --rm -t \
			-v "$(PWD)":/workspace:Z \
			-w /workspace \
			docker.io/library/python:3.14-bookworm \
			bash -lc "python -m venv .venv && .venv/bin/pip install -q -e .[dev] && .venv/bin/python -m pytest -q tests/test_bootstrap_pty.py -m 'bootstrap_deep' -k 'not leak'"; \
	else \
		echo "podman is required for test-bootstrap-rootlike"; \
		exit 1; \
	fi

lint:
	@$(BOOTSTRAP_DEV)
	$(RUN) ruff check .

typecheck:
	@$(BOOTSTRAP_DEV)
	$(RUN) mypy --strict src

check: lint typecheck test
