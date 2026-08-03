# Task model and idempotency

## Idempotency

Each task must be idempotent:
repeated runs must not destroy an already configured system

If target state is already reached, a task normally skips changes.

Tasks must support force mode that reruns a task even after completion.

## Task configuration

Per-task configuration must define:
what the task does

## Structure

Preferred structure:
each task in a separate file
dedicated tasks directory

Task file name must match task name in configuration and documentation

Task data is stored in a shared task-data directory, in a subdirectory matching task name.

## Example

A meaningful task: install and configure SSH server, patch daemon config, add pre-generated certificates for passwordless login.

## Task contract (Python)

A task is implemented as a function that accepts RunContext and optional typed parameters.

Task return type: TaskResult (dataclass) with fields:
success
changes made
error text (if any)

Data transfer between tasks is explicit only:
through API of objects inside RunContext (e.g., secrets store)
or through orchestrator passing required values as arguments to the next task

Hidden data exchange via shared mutable state outside RunContext and outside arguments is forbidden.

For task contract use typing.Protocol (structural typing), not mandatory ABC inheritance.
