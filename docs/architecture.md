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

Bootstrap transport contract for `curl | sudo bash` style launches:

- Before starting the interactive CLI, `i.sh` must reconnect stdin to the controlling terminal via `/dev/tty`.
- The reconnect guard is: `if [ -t 0 ] || [ -e /dev/tty ]; then exec < /dev/tty; fi`.
- If `/dev/tty` is unavailable (for example: cron, ssh without `-t`, some CI runners), bootstrap must log the reason and switch to an explicit non-interactive fallback.
- This contract avoids silent hangs when interactive selectors cannot safely read terminal input.

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

## 8. Secrets management

### 8.1 Vault files

Two KeePass vault files live under `secrets/`:

| File | Purpose | In git? |
|------|---------|---------|
| `secrets/default.vault` | Test/stub secrets for development and CI | Yes |
| `secrets/production.vault` | Real secrets for production deployment | Yes |

### 8.2 Password files

Each vault has a companion password file with the same base name and `.password` extension:

| File | Purpose | In git? |
|------|---------|---------|
| `secrets/default.password` | Password for `default.vault` | Yes |
| `secrets/production.password` | Password for `production.vault` | **No** (`.gitignore`) |

The password file contains a single line of text — the vault password, with no trailing whitespace.

### 8.3 Resolution order

`VaultSecretsStore.load()` resolves the vault and password in this order:

1. **Determine target vault:** `--use-production-secrets` selects `production.vault`; otherwise `default.vault`.

2. **Resolve password source:**
   - `PYNTARA_VAULT_PASSWORD` env var — overrides everything, used for non-interactive bootstrap.
   - `<vault-path>.password` file — e.g. `secrets/production.password` or `secrets/default.password`.
   - Interactive prompt — only for `production.vault` when no password file exists (see 8.6).

3. **Open the vault.**
   - If `production.vault` opens successfully → use it.
   - If `production.vault` fails (wrong password) → **fall back to `default.vault`** with its own password.
   - If `default.vault` fails → error (password attempts exhausted).

### 8.4 Interactive prompt with timeout

When `production.vault` is selected but `secrets/production.password` does not exist:

1. Print a prompt on the terminal: `"Enter production vault password (auto-fallback to default in 11s): "`
2. Start an 11-second countdown displayed in real time on the same line (like the mode selector).
3. The user can start typing the password at any time during the countdown.
4. Once the user presses the first key, the countdown disappears and hidden input mode begins.
5. If the user does not press any key within 11 seconds → automatically fall back to `default.vault`.
6. If the user enters a password → try to open `production.vault`. If wrong → fall back to `default.vault`.

### 8.4 Password file format

```
<password>\n
```

Single line, no trailing whitespace. The file is read with `Path.read_text(encoding="utf-8").strip()`.

### 8.5 Environment variable override

`PYNTARA_VAULT_PASSWORD` env var overrides the password file for both vaults.
This is used for non-interactive bootstrap (`curl ... | sudo bash`).

### 8.6 Interactive prompt

If neither the password file nor the env var provides a password, and the terminal supports hidden input, the user is prompted interactively via `_read_password_hidden()` (direct `termios` access to `/dev/tty` with foreground process group management).

### 8.7 Security notes

- `production.password` must never be committed to git. It is listed in `.gitignore`.
- `default.password` contains a well-known test password and is safe to commit.
- The production vault may contain real credentials (API tokens, SSH keys, etc.).
- Password files are read into memory and the password is never written to logs.
