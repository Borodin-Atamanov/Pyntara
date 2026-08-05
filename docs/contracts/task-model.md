# Task model and idempotency

## Idempotency

Each task must be idempotent:
repeated runs must not destroy an already configured system

If target state is already reached, a task normally skips changes.

Tasks must support force mode that reruns a task even after completion. Force mode is a list of task names (PYNTARA_FORCE_TASKS); a forced task reruns even when the target state is already reached.

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

The catalog lives in config.toml under the [[tasks]] section. Each entry has name, description, dependencies and mode membership; dependencies must name tasks listed earlier in the file. The file is the single source of truth for task names: the docs never repeat the catalog, so a rename in the config cannot leave stale names behind. Per-task behavior is described in the spec documents (docs/spec/).

## Task dependencies

Enabling a task auto-enables all its required dependencies transitively.
Disabling a task does not auto-disable dependent tasks.
Task set and metadata are defined in config.toml under the [[tasks]] section; task_catalog.py holds the resolution logic.

## Task contract (Python)

A task is a plain function task(ctx) -> TaskResult.

The runner must print an empty line and the task title before each task, pause 0.5 seconds, then execute the task with real-time output and print a completion report line with the task status and the details from the result after it finishes.

TaskResult is a dataclass with fields:
success
changed
skipped (optional; True when the task module is not implemented)
message (optional)
error (optional)

Data transfer between tasks is explicit only:
through Context fields (e.g., secrets)
or through the orchestrator passing required values as arguments to the next task

Hidden data exchange via shared mutable state outside Context and outside arguments is forbidden.

No typing.Protocol, no ABC inheritance.
