# Coding standards

These standards are mandatory for this repository.

## Typing and static analysis

1. All production code must use explicit type hints.
2. `mypy` must run in strict mode.
3. New code must pass static type checks before merge.

## Formatting and linting

1. `ruff` is the required formatter and linter.
2. Code style violations must be fixed in the same pull request.

## External command safety

1. `subprocess` with `shell=True` is forbidden.
2. Every external command must have explicit return-code validation.
3. Command arguments must be passed as argument lists, not shell strings.

## Idempotency requirements

1. Every setup operation must be idempotent.
2. Repeated execution must not break an already configured system.
3. Repeated execution must not overwrite generated secrets unless explicit rotation is requested.

## Secret and telemetry constraints

1. Hardcoded secrets are forbidden.
2. Hardcoded telemetry destination paths are forbidden.
3. Secret values must never be logged in plain text.
