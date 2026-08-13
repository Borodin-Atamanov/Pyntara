# Task model and idempotency

This contract fixes the task model: what a task is, how tasks are declared, ordered and stored, and how idempotency is enforced. The runtime task contract (signature, TaskResult fields, presentation) lives in docs/contracts/architecture.md section 5 and docs/guides/project-rules.md section 1.1; this document covers the model and the catalog.

## Idempotency

Each task must be idempotent:
repeated runs must not destroy an already configured system

If target state is already reached, a task normally skips changes.

Tasks must support force mode that reruns a task even after completion. Force mode is a list of task names (PYNTARA_FORCE_TASKS); a forced task reruns even when the target state is already reached. The keyword all forces every task of the resolved run set. Task names and the keyword are case-insensitive.

## Task configuration

Per-task configuration must define:
what the task does

## Structure

Each task is a separate Python module in src/pyntara/tasks/.
Task file name must match task name in the catalog.
Task data is stored in the task-data directory, in a subdirectory matching the task name.

## Example

A meaningful task: install and configure SSH server, patch daemon config, add pre-generated certificates for passwordless login.

## Task catalog

The catalog lives in the config/ directory under the [[tasks]] section (tasks.toml). Each entry has name, description, dependencies and mode membership; dependencies must name tasks listed earlier in the file. The config is the single source of truth for task names: the docs never repeat the catalog, so a rename in the config cannot leave stale names behind. Per-task behavior is described in the spec documents (docs/spec/).

## Task dependencies

Enabling a task auto-enables all its required dependencies transitively.
Disabling a task does not auto-disable dependent tasks.
Task set and metadata are defined in the config/ directory under the [[tasks]] section; task_catalog.py holds the resolution logic.

## Task contract (Python)

The runtime contract of a task is fixed by docs/contracts/architecture.md section 5: a task is a plain function task(ctx) -> TaskResult, and TaskResult carries the fields defined there (success, changed, skipped, message, error). The presentation contract (banner, pause, outcome line) is fixed by docs/guides/project-rules.md section 1.1.

Data transfer between tasks is explicit only:
through Context fields (e.g., secrets)
or through the orchestrator passing required values as arguments to the next task

Hidden data exchange via shared mutable state outside Context and outside arguments is forbidden.
No typing.Protocol, no ABC inheritance.
