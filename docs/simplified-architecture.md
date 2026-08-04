# Simplified architecture (proposal for approval)

This document proposes a simplified runtime architecture for Pyntara.
It replaces the design in docs/contracts/architecture.md and docs/contracts/task-model.md once approved.

## 1. Why simplify

The contract architecture describes an enterprise-style runtime: a config precedence chain, a DI framework with frozen RunContext and read-only catalog wrappers, a task state machine with JSON status files and fingerprints, and a text protocol between inst.sh and the Python engine. These layers protect large codebases with many developers. Pyntara is a personal, non-interactive provisioning tool run as root by one person. The layers add fragility without adding value: every extra protocol, state file and abstraction is a place where a failure can hide.

## 2. What changed

1. Dialog layer removed. dialog and bsdutils packages, select_tasks, select_install_mode, prompt_password_input, load_task_catalog, resolve_tasks, the task-catalog command and tasks.yaml are gone. The task catalog lives in code in src/pyntara/task_catalog.py. inst.sh passes only environment variables; the engine resolves defaults and dependencies inside the process. This removes the most fragile protocol in the project: the shell no longer parses Python output.
2. Task state machine removed. No JSON state files, no statuses, no input fingerprints. Idempotency is achieved the classic way: each task checks the real system state and skips when the goal is already reached. Force mode is a list of tasks that must be rerun even when the target state is already reached: PYNTARA_FORCE_TASKS, space-separated task names. Invalid names are reported with a notice and filtered out; the run continues (section 7).
Уточнение: если передан список задач, а у нас нет нет задачи из этого списка, то показывает ошибка пользователю, 7 секунд она отображается, а дальше продолжается выполнение. Логика такая: если пользователю нужно - он прервёт выполнение и заново определит переменные окружения, а если нет - то продолжит выполнение. Это относится и к списку задач и к списку forced-задач.

3. Config precedence chain removed. No file config, no CLI options, no Pydantic config models. The engine reads validated environment variables: PYNTARA_INSTALL_MODE, PYNTARA_TASKS, PYNTARA_VAULT_PASSWORD, PYNTARA_VAULT_SOURCE, PYNTARA_FORCE_TASKS. Invalid values never stop the run: they follow the resilience rule (section 7).
4. DI framework removed. No typing.Protocol, no task registry, no read-only catalog wrapper. One small frozen Context dataclass carries install mode, vault credentials, the force task list and the task data root. Tasks are plain functions task(ctx) -> TaskResult, one module per task in src/pyntara/tasks/.
5. Logging to stdout. The engine prints to stdout; inst.sh already tees all output into /var/log/pyntara/install.log (bootstrap contract section 9). No syslog handler, no masking filter. The rule is simpler than a filter: never log secret values.

## 3. Engine structure

- pyntara.py: command entry, check-vault, run. run is the composition root: it reads the environment, validates it, builds Context and launches the runner.
- task_catalog.py: TASKS list with name, description, dependencies and mode membership; validate_mode, default_tasks, resolve.
- models.py: TaskResult dataclass (success, changed, skipped, message, error).
- context.py: Context frozen dataclass.
- task_runner.py: loads task modules by name, runs them in order, collects results. A missing module is a skipped result, a broken module is a failed result; neither crashes the run, and the summary shows everything that was skipped or failed.
- tasks/<name>.py: one module per task, each exposing task(ctx) -> TaskResult.
- tests/: pytest for the engine, bash tests for inst.sh.

## 4. What stays unchanged

- inst.sh bootstrap core: root check, FHS directories, optimistic apt, uv install, git fetch, uv sync, vault password resolution through check-vault, install mode detection.
- check-vault command and its tests.
- mypy --strict and ruff mandatory; pytest covers both the Python application and the bootstrap installer.
- The 15 tasks from the catalog as the main implementation work.

## 5. What is next (separate changes)

- config_edit.py: managed-block editing helper for targeted config changes.
- secrets_store.py: controlled KeePass access for tasks that need vault values. Все секреты при запуске загружаются и становятся доступны всем задачам. Нет такого, что задача запрашивает секрет и получает его потом
- systemd.py and telemetry.py with the telemetry_setup task.

## 6. Documentation updates (completed)

- docs/contracts/architecture.md: rewritten to the simplified module map.
- docs/contracts/task-model.md: state machine removed, idempotency and the in-code task catalog kept.
- docs/contracts/interactive-ui.md: deleted.
- docs/guides/project-structure.md: dropped the removed modules.
- docs/spec/install-modes.md and docs/contracts/bootstrap.md: task-catalog references dropped.

## 7. Resilience rule

The program must keep working whenever it can and must not crash on recoverable input errors. If an environment-provided value is invalid, the engine shows an error notice that names the problem and the applied fallback, waits a visible countdown (default 7 seconds, displayed as plain numbers without a unit letter) so the user can interrupt with Ctrl-C and fix the environment, then continues with a safe fallback.

1. Unknown install mode in PYNTARA_INSTALL_MODE: apply the auto-detected default (desktop when a desktop session is present, otherwise server). A missing PYNTARA_INSTALL_MODE is not an error either: the mode is auto-detected and reported.
2. Unknown task name in PYNTARA_TASKS: continue without the unknown name.
3. Invalid task name in PYNTARA_FORCE_TASKS (unknown or not part of the run set): continue without the invalid name.

Only a condition with no possible fallback stops the program.
