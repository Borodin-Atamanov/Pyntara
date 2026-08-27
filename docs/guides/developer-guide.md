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

Minimum required per task: one success scenario test and one realistic error scenario test (for example, a command unavailable or permission denied).

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

Add a [[tasks]] entry to config/tasks.toml with name, description, dependencies and modes. Dependencies must name tasks listed earlier in the file (docs/contracts/task-model.md).  
Create src/pyntara/tasks/<name>.py with a task(ctx) -> TaskResult function ([Task contract](../contracts/architecture.md#task-contract)). Import shared helpers from utils.py, config_edit.py or domain modules (i2pd.py, yggdrasil.py, tor.py, ssh.py, nextdns_profile.py) instead of reimplementing.  
If the task needs configuration values, add a [<name>] section to a new or existing TOML file in config/ and wire the parser in src/pyntara/config/ ([Adding a new config section](project-structure.md#adding-a-new-config-section)).  
If the task needs runtime data files, create a task_data/<name>/ directory.  
Write tests in tests/test_<name>.py: at minimum one success scenario and one realistic error scenario. Use shared factories from tests/support.py (make_config, make_context, FakeProc). Mock external resources via monkeypatch ([Testing rules](#testing-rules)).  
If the task belongs to a default install mode, verify that the mode lists it in tasks.toml and that the dependency chain is complete.

## Task best practices

These rules come from the kde_keyboard_setup hotkey work, where writing a config file alone did not make a setting work. They apply to any task that configures a running service or a desktop session.

Find how a setting takes effect before writing files. A value a daemon reads only at session start does not apply live; the mechanism is a file read, a DBus call, or a reload signal. Design the task around the real mechanism.  
Identify the process that owns the state. A DBus service name can be served by an unexpected process (kwin serves org.kde.kglobalaccel on Wayland). Check the owner with GetConnectionUnixProcessID before planning restarts or reloads.  
Never trust a silent DBus success. A void method can no-op on a wrong argument without an error. Read the state back after every state-changing call.  
Take the exact contract from the source. Wrong argument order is a common silent failure; verify field order in the daemon headers or implementation, not by guessing.  
Prove the client before building on it. Some DBus clients cannot marshal nested types such as a(ai). Test the chosen client on the live system first.  
Replicate the GUI conflict handling. A daemon rejects an occupied key silently. Find the current owner, clear it, then assign.  
Separate deciding from executing. Keep decision logic in the task where tests can reach it, keep embedded subprocess scripts thin, and run those scripts in the same runtime they use in production (the target user, not the dev environment).  
Keep idempotency through read-back. Compare the current value before writing and use the before/after state to decide whether anything changed.  
Respect the side that persists state. If the daemon saves on its own, do not duplicate the file write and race its autosave; write the file only for the no-session path.  
Use stable identifiers in config keys. Prefer unique names over localized display names or codes that need a fragile mapping.  
Declare runtime dependencies in the task and install them. Do not assume a client library exists on the target.  
Document limitations honestly. State what is not applied automatically (conflicts without a session, unsupported forms) instead of claiming full behavior.

## Planning a task

The full planning procedure is defined in [planning-procedure.md](planning-procedure.md). It is mandatory when the user says "plan". This section summarises the key principles that apply to every task, with or without a formal plan.

Every task has two goals: the described goal (what the config or spec says) and the implied goal (what the user experiences after the task). The implied goal is the acceptance test; the described goal only serves it.

Research on the machine before writing code. Run small reversible probes to establish facts: who owns the state, which tool or client works, what the exact call is. Never guess a mechanism a probe can settle in minutes, and never run a probe that disrupts the running session (restarting kwin or the Wayland session is forbidden).

Make the plan proportional to uncertainty: when the mechanism is known, keep it short; when unknown, the first stage of the plan is the probe. After implementation, verify on the same machine: run the task and check the implied goal live, not only the unit tests.
