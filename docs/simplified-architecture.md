# Simplified architecture (architecture decision record)

This record fixes the simplified runtime architecture of Pyntara. The decision is approved and implemented: the enterprise-style design described in docs/contracts/architecture.md and docs/contracts/task-model.md was replaced, and both contracts now describe the simplified module map (section 6). The record keeps the rationale and the decisions behind the change.

## 1. Why simplify

The contract architecture describes an enterprise-style runtime: a config precedence chain, a DI framework with frozen RunContext and read-only catalog wrappers, a task state machine with JSON status files and fingerprints, and a text protocol between inst.sh and the Python engine. These layers protect large codebases with many developers. Pyntara is a personal, non-interactive provisioning tool run as root by one person. The layers add fragility without adding value: every extra protocol, state file and abstraction is a place where a failure can hide.

## 2. What changed

1. Dialog layer removed. dialog and bsdutils packages, select_tasks, select_install_mode, prompt_password_input, load_task_catalog, resolve_tasks, the task-catalog command and tasks.yaml are gone. The task catalog lives in config.toml under the [[tasks]] section. inst.sh passes only environment variables; the engine resolves defaults and dependencies inside the process. This removes the most fragile protocol in the project: the shell no longer parses Python output.
2. Task state machine removed. No JSON state files, no statuses, no input fingerprints. Idempotency is achieved the classic way: each task checks the real system state and skips when the goal is already reached. Force mode is a list of tasks that must be rerun even when the target state is already reached: PYNTARA_FORCE_TASKS, space-separated task names. The keyword all (case-insensitive) forces every task of the resolved run set. Task names and the keyword are case-insensitive. Invalid names are reported with a notice and filtered out; the run continues (section 7). The same notice applies to PYNTARA_TASKS: when the list contains a task that is not in the catalog, an error notice is shown for the configured countdown, so the user can interrupt the run and redefine the environment variables; without an interrupt the run continues without the unknown names. This applies to both the task list and the force list.

3. Single config file added as the source of truth for the Python part. config.toml at the repository root holds the engine values and the task catalog: task data root, notice timeout, per-task data such as the cli_tools package list, and one [[tasks]] entry per task with name, description, dependencies and mode membership. The file is mandatory: a missing or invalid file stops the run, there are no defaults. Environment variables remain the inst.sh interface for per-run selection (mode, tasks, force) and secrets. Env-over-config priority is deferred. Invalid environment values never stop the run: they follow the resilience rule (section 7).
4. DI framework removed. No typing.Protocol, no task registry, no read-only catalog wrapper. One small frozen Context dataclass carries install mode, vault credentials, the force task list and the task data root. Tasks are plain functions task(ctx) -> TaskResult, one module per task in src/pyntara/tasks/.
5. Logging to stdout with the journal as the primary destination. The system journal is the primary destination for own messages: the engine mirrors them through src/pyntara/logger.py with the identifier pyntara-engine, inst.sh mirrors its own with the identifier pyntara-install. The file log is residual: inst.sh tees the full stream into /var/log/pyntara/install.log (bootstrap contract section 9) for offline review. No masking filter. The rule is simpler than a filter: never log secret values.

## 3. Engine structure

1. pyntara.py: command entry, check-vault, run. run is the composition root: it reads the environment, validates it, builds Context and launches the runner.
2. task_catalog.py: validate_mode, default_tasks, resolve, unknown_tasks operating on the catalog loaded from config.toml.
3. models.py: TaskResult dataclass (success, changed, skipped, message, error).
4. context.py: Context frozen dataclass.
5. config.py: loads and validates config.toml into a frozen Config dataclass; the composition root reads it and hands it to tasks through Context.
6. task_runner.py: loads task modules by name, runs them in order, collects results. A missing module is a skipped result, a broken module is a failed result; neither crashes the run, and the summary shows everything that was skipped or failed.
7. tasks/<name>.py: one module per task, each exposing task(ctx) -> TaskResult.
8. tests/: pytest for the engine, bash tests for inst.sh.

## 4. What stays unchanged

1. inst.sh bootstrap core: root check, FHS directories, optimistic apt, uv install, git fetch, uv sync, vault password resolution through check-vault, install mode detection.
2. check-vault command and its tests.
3. mypy --strict and ruff mandatory; pytest covers both the Python application and the bootstrap installer.
4. The 17 tasks from the catalog as the main implementation work.

## 5. What is next (separate changes)

1. secrets_store.py: controlled KeePass access for tasks that need vault values. All secrets are loaded at startup and become available to all tasks; no task requests a secret and receives it later.
2. systemd.py and system_metrics.py with the System Metrics delivery task.

The system_metrics_setup task was implemented as the first separate change: it deploys the long-running System Metrics service on the target machine (docs/contracts/architecture.md section 6). The config editing helper src/pyntara/config_edit.py was implemented as the second separate change; its description lives in docs/guides/project-structure.md, section Configuration editing.

## 6. Documentation updates (completed)

1. docs/contracts/architecture.md: rewritten to the simplified module map.
2. docs/contracts/task-model.md: state machine removed, idempotency and the config-driven task catalog kept.
3. docs/contracts/interactive-ui.md: deleted.
4. docs/guides/project-structure.md: dropped the removed modules.
5. docs/spec/install-modes.md and docs/contracts/bootstrap.md: task-catalog references dropped.

## 7. Resilience rule

The program must keep working whenever it can and must not crash on recoverable input errors. If an environment-provided value is invalid, the engine shows an error notice that names the problem and the applied fallback, waits a visible countdown (default 7 seconds, displayed as plain numbers without a unit letter) so the user can interrupt with Ctrl-C and fix the environment, then continues with a safe fallback.

1. Unknown install mode in PYNTARA_INSTALL_MODE: apply the auto-detected default (desktop when a desktop session is present, otherwise server). A missing PYNTARA_INSTALL_MODE is not an error either: the mode is auto-detected and reported.
2. Unknown task name in PYNTARA_TASKS: continue without the unknown name.
3. Invalid task name in PYNTARA_FORCE_TASKS (unknown or not part of the run set): continue without the invalid name.

Only a condition with no possible fallback stops the program.
