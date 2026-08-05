# Developer guide

## Quick start

Clone the repository.
Run uv sync to set up the Python environment.
Run uv run pytest to execute the test suite.
Run uv run ruff check . for linting.
Run uv run mypy --strict src/ for type checking.

## Testing rules

Every module with task logic must have pytest unit tests.
In unit tests, all external resources (subprocess, filesystem, network) are mocked via monkeypatch.
For file logic, use tmp_path, not real paths.
Shared test factories and fakes live in tests/support.py (make_config, make_context, FakeProc); test modules import them instead of copying the Config and Context shapes.

Minimum required per task:
1 success scenario test
1 realistic error scenario test (e.g., command unavailable or permission denied)

Secrets store must have a test proving that reloading an existing store returns the same values (no regeneration).

Testing MUST cover both the Python application and the bootstrap installer.

## CI requirements

Project must enforce ruff, mypy --strict, and full pytest.
Pushing to repository without these checks is not allowed.

## Commit workflow

Before commit, run the full test suite and fix all failures until green.
After finishing changes, integrate them into main.
Any change that breaks architecture guarantees must update docs/contracts/architecture.md and corresponding tests in the same pull request.
