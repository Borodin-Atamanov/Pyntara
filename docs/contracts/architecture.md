# Architecture baseline

This document fixes the mandatory runtime architecture for Pyntara.
All new modules and tasks must follow this contract.

## 1. Runtime boundaries

inst.sh - bootloader
pyntara.py (composition root and command entry).
config_loader.py (configuration normalization and validation).
context.py (RunContext construction and dependency wiring).
task_runner.py (task discovery and execution).

## 2. Composition root

The run command in pyntara.py is the only place that is allowed to assemble runtime state:

resolve configuration from defaults, file, env, and CLI overrides
initialize logging
initialize secret storage
create RunContext
create registry/runner and execute selected tasks

No task may create its own global singleton for config, secrets, or logging.

## 3. Configuration contract

config_loader.load_runtime_configuration resolves config with this strict precedence:

CLI flags > environment variables > config file > built-in defaults

The merged payload is validated by Pydantic models from models.py.
Invalid schemas must fail fast with explicit errors.

## 4. RunContext contract

RunContext in context.py dataclass(frozen=True)).
It is the only carrier for cross-cutting runtime dependencies:

resolved AppConfig
install mode config
task catalog
secret store API
logger
task data root directory

Task catalog is wrapped in a read-only mapping to prevent accidental mutation.

RunContext is passed explicitly through calls.
Implicit state reads from os.environ (except dedicated components), module-level variables, and other hidden sources are forbidden.

## 5. Task contract

Task entrypoints use structural typing (typing.Protocol) from task_protocol.py:

(ctx: RunContext, *, force: bool = False) -> TaskResult

TaskResult is a dataclass with explicit outcome fields:

success
changed
optional message
optional error

Task-to-task data sharing is allowed only through explicit arguments or RunContext dependencies.

Task definitions are declarative manifests loaded from tasks.yaml.
Each task manifest includes runtime execution metadata such as:

dependencies (depends_on)
conflicts (conflicts_with)
capability requirements (requires_root, requires_network, requires_secrets)
timeout and state schema version (timeout_sec, state_version)
idempotency control flags

Full task model contract: docs/contracts/task-model.md.

## 6. State and side effects

Allowed explicit shared state channels:

encrypted vault files (secrets/*.vault)
task state files under task_data/<task-name>/

Task state is persisted in JSON and must keep at least:

status (pending, running, done, failed, skipped)
run timestamps and attempt counter
input fingerprint for idempotent skip decisions
structured error and result fields

The only allowed shared state outside one process memory is explicit external channels:

encrypted secrets storage file
telemetry file queue
named IPC command channels

Exchange boundaries between processes of different systemd services must be explicitly documented as an architecture contract.

Forbidden patterns:

module-level mutable state for runtime business data
implicit os.environ reads inside task modules
hidden data exchange via ad-hoc globals

## 7. Typing and architecture patterns

For task contract use typing.Protocol (structural typing), not mandatory ABC inheritance.

Stateful classes are allowed where encapsulation is truly needed (example: telemetry delivery client).
Such classes are created once at entrypoint and passed via RunContext, not recreated inside tasks (dependency injection).

Using stdlib logging with module-named logger and SysLogHandler to system journal is an allowed exception to shared-state restrictions, because this is infrastructure layer, not business logic.

## 8. Guardrails

The architecture is guarded by tests:

config precedence tests
RunContext immutability and read-only catalog tests
task runner idempotency/force behavior tests

Any change that breaks these guarantees must update this document and corresponding tests in the same pull request.

## 9. Secrets management

Full secrets model specification: docs/spec/secrets-model.md.

### 9.1 Vault files

Two KeePass vault files live under secrets/:

secrets/default.vault — Test/stub secrets for development and CI. In git.
secrets/production.vault — Real secrets for production deployment. In git.

### 9.2 Password files

Each vault has a companion password file with the same base name and .password extension:

secrets/default.password — Password for default.vault. In git.
secrets/production.password — Password for production.vault. Not in git (.gitignore).

The password file contains a single line of text — the vault password, with no trailing whitespace.

### 9.3 Resolution order

VaultSecretsStore.load() resolves the vault and password in this order:

Determine target vault: --use-production-secrets selects production.vault; otherwise default.vault.
Resolve password source:
PYNTARA_VAULT_PASSWORD env var — overrides everything, used for non-interactive bootstrap.
<vault-path>.password file — e.g. secrets/production.password or secrets/default.password.
Interactive prompt — only for production.vault when no password file exists.
Open the vault. If production.vault fails, 3 attempts. After 3 failures, fall back to default.vault. If both fail, exit with error.

### 9.4 Password file format

Single line, no trailing whitespace. Read with Path.read_text(encoding="utf-8").strip().

PYNTARA_VAULT_PASSWORD env var overrides the password file for both vaults.

### 9.5 Interactive prompt via dialog

When production.vault is selected and no password file or env var provides the password:

Show dialog --passwordbox with an 11-second countdown.
If user presses any key, the countdown stops and the user types the password.
If no key is pressed within 11 seconds, fall back to default.vault immediately.
On wrong password, show error via dialog --msgbox and prompt again (up to 3 attempts).
After 3 failures, fall back to default.vault with default.password.

KeePass decryption is done by a Python library, not shell tools.

### 9.6 Security notes

production.password must never be committed to git. It is listed in .gitignore.
default.password contains a well-known test password and is safe to commit.
The production vault may contain real credentials (API tokens, SSH keys, etc.).
Password files are read into memory and the password is never written to logs.
