# Developer guide

## Quick start

Clone the repository.  
Install the interpreter of the distribution: the project requires Python 3.14, which Kubuntu 26.04 ships as python3.14, and uv is configured to prefer it over a CPython it downloaded itself.  
Run uv sync to set up the Python environment.  
Run uv run pytest to execute the test suite.  
Run uv run ruff check . for linting.  
Run uv run mypy --strict src/ for type checking.  
Run scripts/check_gates.sh to run every gate of this page in one command: the linting, both type checks, the test suite and the five bash suites.  
Add --fast to check only the touched python files and the test modules that match them by name, which is the command of the development loop; the full run stays the check before a landing.  
The py.typed marker in src/pyntara lets the bare uv run mypy type-check the tests as well, so a type regression in a test helper is caught by default.

The test suite runs in parallel through pytest-xdist: [tool.pytest.ini_options] addopts in pyproject.toml is -n auto, so uv run pytest spreads the tests over worker processes on its own, and the worker count follows the machine. Use uv run pytest -n 0 for a serial run when measuring where the time goes, because under workers the wall time no longer maps to a single test.

## Testing rules

Every module with task logic must have pytest unit tests.  
In unit tests, all external resources (subprocess, filesystem, network) are mocked via monkeypatch.  
A test never reaches the systemd of the machine it runs on: the real systemctl acts on the units deployed there and, without root, opens the polkit password dialog of the desktop session, which waits for an answer no test can give. The suite replaces the subprocess of the module under test with a recorded fake, and the autouse guard in tests/conftest.py fails the test that forgot, naming the command it tried to run.  
For file logic, use tmp_path, not real paths.  
Shared test factories and fakes live in tests/support.py (make_context, FakeProc); test modules import them instead of copying the Context shape. A test that needs another value patches the declared name on the values module of the task, so the shipped value comes back when the test ends and no copy of the value exists in the suite.  
Journal forwarding is covered by integration tests in tests/test_logger.py and tests/test_inst.sh that write into the real system journal through systemd-cat and read entries back through journalctl. When journald is unavailable the tests skip; the best-effort branches are always covered by unit tests. A test never waits out real time to learn an outcome: a patched time.sleep paces a retry loop, a fake child process is dropped as soon as it delivered its output, and an absent journal line is proved by sending a later marker line through the same pipe instead of polling a fixed window.

Minimum required per task: one success scenario test and one realistic error scenario test (for example, a command unavailable or permission denied).

Secrets store must have a test proving that reloading an existing store returns the same values (no regeneration).

Testing MUST cover both the Python application and the bootstrap installer.

## CI requirements

Project must enforce ruff, mypy --strict, and full pytest.  
Pushing to repository without these checks is not allowed.  
Every push and every pull request runs .github/workflows/checks.yml, which calls scripts/check_gates.sh, so the pipeline checks the same set the [quick start](#quick-start) lists and never a second copy that drifts from it.  
That run reports after the push, so it shows a red commit instead of stopping it; keeping a red commit out of main needs a status check in the repository settings of GitHub, which is not a file in the tree.  
The workflow takes Python 3.14 through actions/setup-python and sets UV_PYTHON_DOWNLOADS to never, so the pipeline runs the interpreter the fleet runs and uv never downloads one itself.

## Commit workflow

Before commit, run the full test suite and fix all failures until green.  
After finishing changes, integrate them into main.  
Any change that breaks architecture guarantees must update docs/contracts/architecture.md and corresponding tests in the same pull request.

## Version bumping

The version grows with every commit and is written into three files, one writer per carrier, so two branches never fight over the same line.

The build carrier is src/pyntara/_version.py, a file whose whole content is the single line __version__; pyproject.toml reads it through hatchling and src/pyntara/__init__.py re-exports it as pyntara.__version__. The pre-commit hook (hooks/pre-commit) bumps it before every commit on every branch, and it is the only writer of that file.

.gitattributes marks the build carrier merge=union. Two branches that both grew the number therefore merge, rebase or cherry-pick without a conflict: git keeps both lines, the version tool reads the highest number of the file and rewrites it as a single line on its next run. The artifact of a union merge is a carrier that holds two numbers for a moment, never a stopped merge.

The installed face of the version is two whole line machine owned carriers that only the landing step writes: the PYNTARA_VERSION line of inst.sh and the title line of README.md. The installer line is a literal because inst.sh prints it as the very first line on a bare machine, where there is no clone, no python and no git to derive it from, and it must name the revision the installer is about to fetch. The README title keeps the number in the form it always had, and the landing step replaces that whole line.

The landing step (hooks/land_version_commit.sh) runs on the branch tip right before the push to main:

hooks/land_version_commit.sh

It bumps the build carrier, mirrors the number into inst.sh and README.md, and records all three as one commit with the subject version: bump to VERSION, so work staged by another agent in the same clone is never swept in. It stops loudly where the pre-commit hook is best-effort: it refuses to run while a carrier has uncommitted changes, it fails when a carrier does not carry the new number after the bump (a version line rewritten by hand), and a failed commit leaves the bumped files in the working tree. The commit skips the hook, because the hook would bump the build carrier a second time and the three carriers must land on one number. The step works on the repository of the current directory, so a linked worktree lands its own branch.

The hook is local to a clone; enable it once with git config core.hooksPath hooks.

The bash suites cover both halves on temporary git repositories: bash tests/test_pre_commit_hook.sh for the per-commit bump and bash tests/test_land_version_commit.sh for the landing step, alongside bash tests/test_inst.sh and bash tests/test_launcher.sh.

## Adding a new task

Add a record to the catalog in src/pyntara/values/tasks.py with name, description, dependencies and modes. Dependencies must name tasks listed earlier (docs/contracts/task-model.md).  
Create src/pyntara/tasks/<name>.py with a task(ctx) -> TaskResult function ([Task contract](../contracts/architecture.md#task-contract)). Import shared helpers from utils.py, config_edit.py or domain modules (i2pd.py, yggdrasil.py, tor.py, ssh.py, nextdns_profile.py) instead of reimplementing.  
If the task needs values, add them to the values module of the task under src/pyntara/values/ and read them at the point of use ([Where a value lives](project-structure.md#adding-a-value-to-an-existing-section)). Every value lives there and nowhere else; a literal that exists both in a values module and in a task body is a defect.  
If the task needs runtime data files, create a task_data/<name>/ directory.  
Write tests in tests/test_<name>.py: at minimum one success scenario and one realistic error scenario. Use shared factories from tests/support.py (make_context, FakeProc). Mock external resources via monkeypatch ([Testing rules](#testing-rules)).  
If the task belongs to a default install mode, verify that the mode lists it in the catalog (src/pyntara/values/tasks.py) and that the dependency chain is complete.

## Adding an install mode

Add the name to MODES in src/pyntara/values/tasks.py and list it in the modes of the records it selects. That is the whole change: the engine validates and resolves every mode from the catalog, a record that belongs to every mode carries modes=MODES and follows a new name without an edit, and the tests name the modes they check one by one, so a mode added later is never a failure in them. Then run python -m pyntara.launcher_modes --root . so the commented mode lines of the launcher file follow the catalog; the same command with --check fails when that file drifted, and tests/test_launcher_modes.py runs it against the shipped file.

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
