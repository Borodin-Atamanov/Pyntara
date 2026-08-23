# Simplified architecture (architecture decision record)

This record fixes the simplified runtime architecture of Pyntara. The decision is approved and implemented: the enterprise-style design described in docs/contracts/architecture.md and docs/contracts/task-model.md was replaced, and both contracts now describe the simplified module map (see [Documentation updates](#documentation-updates-completed)). The record keeps the rationale and the decisions behind the change.

## Why simplify

The contract architecture describes an enterprise-style runtime: a config precedence chain, a DI framework with frozen RunContext and read-only catalog wrappers, a task state machine with JSON status files and fingerprints, and a text protocol between inst.sh and the Python engine. These layers protect large codebases with many developers. Pyntara is a personal, non-interactive provisioning tool run as root by one person. The layers add fragility without adding value: every extra protocol, state file and abstraction is a place where a failure can hide.

## What changed

Dialog layer removed. dialog and bsdutils packages, select_tasks, select_install_mode, prompt_password_input, load_task_catalog, resolve_tasks, the task-catalog command and tasks.yaml are gone. The task catalog lives in the config/ directory under the [[tasks]] section. inst.sh passes only environment variables; the engine resolves defaults and dependencies inside the process. This removes the most fragile protocol in the project: the shell no longer parses Python output.  
Task state machine removed. No JSON state files, no statuses, no input fingerprints. Idempotency is achieved the classic way: each task checks the real system state and skips when the goal is already reached. Force mode and task selection are environment variables resolved by the engine ([Task selection](spec/install-modes.md#task-selection), [Force task selection](spec/install-modes.md#force-task-selection)); invalid names follow the [Resilience rule](#resilience-rule).  
Single config source added as the source of truth for the Python part: the config/ directory at the repository root, one TOML file per top-level section, joined into a single document; a missing or invalid config stops the run, there are no defaults ([Configuration](contracts/architecture.md#configuration)). Environment variables remain the inst.sh interface for per-run selection (mode, tasks, force) and secrets. Env-over-config priority is deferred.  
DI framework removed. No typing.Protocol, no task registry, no read-only catalog wrapper. One small frozen Context dataclass carries install mode, vault credentials, the force task list and the task data root. Tasks are plain functions task(ctx) -> TaskResult, one module per task in src/pyntara/tasks/.  
Logging to stdout with the journal as the primary destination. The system journal is the primary destination for own messages: the engine mirrors them through src/pyntara/logger.py with the identifier pyntara-engine, inst.sh mirrors its own with the identifier pyntara-install. The file log is residual: inst.sh tees the full stream into /var/log/pyntara/install.log ([bootstrap contract, Logging](contracts/bootstrap.md#logging)) for offline review. No masking filter. The rule is simpler than a filter: never log secret values.

## Engine structure

pyntara.py: command entry, check-vault, run. run is the composition root: it reads the environment, validates it, builds Context and launches the runner.  
task_catalog.py: validate_mode, default_tasks, resolve, unknown_tasks operating on the catalog loaded from the config/ directory.  
models.py: TaskResult dataclass (success, changed, skipped, message, error).  
context.py: Context frozen dataclass.  
config/: loads and validates the config/ directory (joined into a single document) into a frozen Config dataclass; the composition root reads it and hands it to tasks through Context.  
task_runner.py: loads task modules by name, runs them in order, collects results. A missing module is a skipped result, a broken module is a failed result; neither crashes the run, and the summary shows everything that was skipped or failed.  
tasks/<name>.py: one module per task, each exposing task(ctx) -> TaskResult.  
tests/: pytest for the engine, bash tests for inst.sh.

## What stays unchanged

inst.sh bootstrap core: root check, FHS directories, optimistic apt, uv install, git fetch, uv sync, vault password resolution through check-vault, install mode detection.  
check-vault command and its tests.  
mypy --strict and ruff mandatory; pytest covers both the Python application and the bootstrap installer.  
The tasks from the catalog (config/tasks.toml) as the main implementation work.

## What is next (separate changes)

secrets_store.py: controlled KeePass access for tasks that need vault values. All secrets are loaded at startup and become available to all tasks; no task requests a secret and receives it later.  
systemd.py and system_metrics.py with the System Metrics delivery task.

The system_metrics_setup task was implemented as the first separate change: it deploys the long-running System Metrics service on the target machine ([Idempotency and side effects](contracts/architecture.md#idempotency-and-side-effects)). The config editing helper src/pyntara/config_edit.py was implemented as the second separate change; its description lives in [Configuration editing](guides/project-structure.md#configuration-editing).

## Documentation updates (completed)

docs/contracts/architecture.md: rewritten to the simplified module map.  
docs/contracts/task-model.md: state machine removed, idempotency and the config-driven task catalog kept.  
docs/contracts/interactive-ui.md: deleted.  
docs/guides/project-structure.md: dropped the removed modules.  
docs/spec/install-modes.md and docs/contracts/bootstrap.md: task-catalog references dropped.

## Resilience rule

The program must keep working whenever it can and must not crash on recoverable input errors. If an environment-provided value is invalid, the engine shows an error notice that names the problem and the applied fallback, waits a visible countdown (default 7 seconds, displayed as plain numbers without a unit letter) so the user can interrupt with Ctrl-C and fix the environment, then continues with a safe fallback.

Unknown install mode in PYNTARA_INSTALL_MODE: apply the auto-detected default (desktop when a desktop session is present, otherwise server). A missing PYNTARA_INSTALL_MODE is not an error either: the mode is auto-detected and reported.  
Unknown task name in PYNTARA_TASKS: continue without the unknown name.  
Invalid task name in PYNTARA_FORCE_TASKS (unknown or not part of the run set): continue without the invalid name.

Only a condition with no possible fallback stops the program.
