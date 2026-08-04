# Architecture baseline

This document fixes the mandatory runtime architecture for Pyntara.
All new modules and tasks must follow this contract.

## 1. Runtime boundaries

inst.sh - bootloader
pyntara.py (command entry and composition root)
task_catalog.py (task metadata, mode defaults, dependency resolution)
context.py (Context construction)
task_runner.py (task discovery and execution)
tasks/*.py (one task per module)

## 2. Composition root

The run command in pyntara.py is the only place that reads the environment and assembles runtime state:

resolve the install mode (PYNTARA_INSTALL_MODE or the auto-detected default)
resolve the task set (PYNTARA_TASKS or the mode defaults, dependencies resolved)
resolve the force task list (PYNTARA_FORCE_TASKS)
create Context
launch the runner

No task may read the environment, create global singletons, or assemble runtime state itself.

## 3. Configuration

There is no config file and no CLI options. All runtime configuration comes from environment variables:

PYNTARA_INSTALL_MODE - minimal, server or desktop. When unset, the mode is auto-detected (desktop when a desktop session or process is present, otherwise server). An unknown value shows the resilience notice and falls back to the auto-detected mode.
PYNTARA_TASKS - space-separated task names. When unset, the mode defaults are used. Unknown names are reported and ignored.
PYNTARA_FORCE_TASKS - space-separated task names that must rerun even when the target state is reached. Invalid names are reported and ignored.
PYNTARA_VAULT_PASSWORD, PYNTARA_VAULT_SOURCE - KeePass credentials resolved by inst.sh.
PYNTARA_NOTICE_TIMEOUT - seconds the resilience notice stays visible (default 7).
PYNTARA_TASK_DATA_DIR - task data root (default /var/lib/pyntara/task-data).

## 4. Context contract

Context in context.py is a frozen dataclass. It is the only carrier for cross-cutting runtime dependencies:

install_mode
vault_password
vault_source
force_tasks (frozenset of task names)
task_data_root (Path)

Context is passed explicitly to every task. Implicit reads of the environment inside task modules are forbidden.

## 5. Task contract

A task is a plain function:

task(ctx) -> TaskResult

TaskResult is a dataclass with fields:

success
changed
message (optional)
error (optional)

No typing.Protocol, no ABC inheritance, no registry.

Task definitions live in code in task_catalog.py. Each entry has name, description, dependencies and mode membership.

Task-to-task data sharing is allowed only through Context fields or explicit arguments.

Full task model contract: docs/contracts/task-model.md.

## 6. Idempotency and side effects

Each task must be idempotent: repeated runs must not destroy an already configured system. A task checks the real system state (user exists, file present, service active) and skips changes when the goal is already reached. Force mode reruns a task even after completion.

Allowed explicit shared state channels:

encrypted vault files (secrets/*.vault)
task data files under task_data/<task-name>/
telemetry file queue
named IPC command channels

Exchange boundaries between processes of different systemd services must be explicitly documented as an architecture contract.

Forbidden patterns:

module-level mutable state for runtime business data
implicit environment reads inside task modules
hidden data exchange via ad-hoc globals

## 7. Resilience rule

The program must keep working whenever it can and must not crash on recoverable input errors. An invalid environment value shows an error notice naming the problem and the applied fallback, waits a visible countdown (plain numbers, default 7 seconds, PYNTARA_NOTICE_TIMEOUT) so the user can interrupt with Ctrl-C, then continues with the fallback. Only a condition with no possible fallback stops the program.

## 8. Guardrails

The architecture is guarded by tests:

task catalog tests (mode defaults, dependency resolution, validation)
entry point tests (mode resolution, task set, force list, resilience rule)
task runner tests (missing modules, failures, continue-on-error)

Any change that breaks these guarantees must update this document and corresponding tests in the same pull request.

## 9. Secrets management

Full secrets model specification: `docs/spec/secrets-model.md`.
Bootstrap-time vault resolution is specified in `docs/contracts/bootstrap.md` (section 12).
