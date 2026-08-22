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
Journal forwarding is covered by integration tests in tests/test_logger.py and tests/test_inst.sh that write into the real system journal through systemd-cat and read entries back through journalctl. When journald is unavailable the tests skip; the best-effort branches are always covered by unit tests.

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

## Version bumping

The pre-commit hook (hooks/pre-commit) bumps the patch version before every commit, so the version in src/pyntara/__init__.py and the PYNTARA_VERSION line of inst.sh grows with each commit. The hook is best-effort: a failure prints a warning and never blocks the commit, so a commit may go through without a version bump. The hook is local to a clone; enable it once with:

git config core.hooksPath hooks

The bash test suite tests/test_pre_commit_hook.sh covers the hook on a temporary git repository; run it with bash tests/test_pre_commit_hook.sh alongside bash tests/test_inst.sh.

## Adding a new task

1. Add a [[tasks]] entry to config/tasks.toml with name, description, dependencies and modes. Dependencies must name tasks listed earlier in the file (docs/contracts/task-model.md).
2. Create src/pyntara/tasks/<name>.py with a task(ctx) -> TaskResult function (docs/contracts/architecture.md section 5). Import shared helpers from utils.py, config_edit.py or domain modules (i2pd.py, yggdrasil.py, tor.py, ssh.py, nextdns_profile.py) instead of reimplementing.
3. If the task needs configuration values, add a [<name>] section to a new or existing TOML file in config/ and wire the parser in src/pyntara/config/ (docs/guides/project-structure.md, Adding a new config section).
4. If the task needs runtime data files, create a task_data/<name>/ directory.
5. Write tests in tests/test_<name>.py: at minimum one success scenario and one realistic error scenario. Use shared factories from tests/support.py (make_config, make_context, FakeProc). Mock external resources via monkeypatch (docs/guides/developer-guide.md, Testing rules).
6. If the task belongs to a default install mode, verify that the mode lists it in tasks.toml and that the dependency chain is complete.
