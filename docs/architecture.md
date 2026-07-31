# Architecture baseline

This document fixes the mandatory runtime architecture for Pyntara.
All new modules and tasks must follow this contract.

## 1. Runtime boundaries

Pyntara is split into four explicit layers:

1. `cli.py` (composition root and command entry).
2. `config_loader.py` + `models.py` (configuration normalization and validation).
3. `context.py` (`RunContext` construction and dependency wiring).
4. `task_registry.py` + `task_runner.py` + `tasks/*` (task discovery and execution).

Each layer depends only on lower-level layers. Tasks must not import from CLI.

## 2. Composition root

The `run` command in `cli.py` is the only place that is allowed to assemble runtime state:

- resolve configuration from defaults, file, env, and CLI overrides;
- initialize logging;
- initialize secret storage;
- create immutable `RunContext`;
- create registry/runner and execute selected tasks.

No task may create its own global singleton for config, secrets, or logging.

## 3. Configuration contract

`config_loader.load_runtime_configuration` resolves config with this strict precedence:

`CLI flags > environment variables > config file > built-in defaults`.

The merged payload is validated by Pydantic models from `models.py`.
Invalid schemas must fail fast with explicit errors.

## 4. RunContext contract

`RunContext` in `context.py` is immutable (`dataclass(frozen=True)`).
It is the only carrier for cross-cutting runtime dependencies:

- resolved `AppConfig`;
- install mode config;
- task catalog;
- secret store API;
- logger;
- task data root directory.

Task catalog is wrapped in a read-only mapping to prevent accidental mutation.

## 5. Task contract

Task entrypoints use structural typing (`typing.Protocol`) from `task_protocol.py`:

`(ctx: RunContext, *, force: bool = False) -> TaskResult`.

`TaskResult` is a dataclass with explicit outcome fields:

- `success`;
- `changed`;
- optional `message`;
- optional `error`.

Task-to-task data sharing is allowed only through explicit arguments or `RunContext` dependencies.

Task definitions are declarative manifests loaded from `tasks.yaml`.
Each task manifest includes runtime execution metadata such as:

- dependencies (`depends_on`);
- conflicts (`conflicts_with`);
- capability requirements (`requires_root`, `requires_network`, `requires_secrets`);
- timeout and state schema version (`timeout_sec`, `state_version`);
- idempotency control flags.

## 6. State and side effects

Allowed explicit shared state channels:

- encrypted vault files (`secrets/*.vault`);
- task state files under `task_data/<task-name>/`.

Task state is persisted in JSON and must keep at least:

- `status` (`pending`, `running`, `done`, `failed`, `skipped`);
- run timestamps and attempt counter;
- input fingerprint for idempotent skip decisions;
- structured error and result fields.

Forbidden patterns:

- module-level mutable state for runtime business data;
- implicit `os.environ` reads inside task modules;
- hidden data exchange via ad-hoc globals.

## 7. Guardrails

The architecture is guarded by tests:

- config precedence tests;
- `RunContext` immutability and read-only catalog tests;
- task runner idempotency/force behavior tests.

Any change that breaks these guarantees must update this document and corresponding tests in the same pull request.
