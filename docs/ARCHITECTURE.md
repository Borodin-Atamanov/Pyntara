# Architecture

This document describes the target architecture using three levels.

## Level 1: System view for the user

Pyntara is a command line tool that helps apply post-installation setup actions on Debian-like systems.
The user selects one feature at a time and confirms execution explicitly.
The tool validates the environment, shows a plan, applies idempotent actions, and reports the result.

## Level 2: Major parts

The system is split into three major parts:

1. **Interface and orchestration layer**
   - Handles command line interaction, confirmations, and execution flow.
2. **Feature and policy layer**
   - Defines feature use cases, input contracts, validation rules, and idempotent behavior contracts.
3. **System integration layer**
   - Interacts with operating system resources such as package manager, service manager, file system, and cryptography tooling through safe wrappers.

## Level 3: Components inside each part

### Interface and orchestration layer

- `Pyntara.cli`:
  Entry point for command line usage.
- `Pyntara.application.feature_runner`:
  Coordinates one approved feature execution and confirmation flow.
- `Pyntara.application.execution_report`:
  Aggregates user-visible results and failure context.

### Feature and policy layer

- `Pyntara.domain.feature_catalog`:
  Declares supported features and their metadata.
- `Pyntara.domain.requirement_contracts`:
  Stores feature contracts aligned with `docs/REQUIREMENTS.md`.
- `Pyntara.domain.idempotency_policy`:
  Defines rules for repeat-safe operations.

### System integration layer

- `Pyntara.infrastructure.package_manager`:
  Safe adapters for package operations.
- `Pyntara.infrastructure.service_manager`:
  Safe adapters for service control.
- `Pyntara.infrastructure.config_repository`:
  Safe file read, write, backup, and merge operations.
- `Pyntara.infrastructure.secret_store`:
  Secret generation and preservation logic boundary.
- `Pyntara.infrastructure.command_runner`:
  External command execution with explicit return-code checks.
- `Pyntara.infrastructure.logging.*`:
  Activity logging components. The exact split into three modules is an open architecture question.

## Design constraints

- No implicit feature execution.
- One approved feature implementation per pull request unless explicitly requested otherwise.
- Strict typing and static checking.
- No `subprocess` calls with `shell=True`.
- Repeat-safe operations so repeated runs do not break configured systems.
