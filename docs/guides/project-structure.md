# Project structure

This document defines the target repository layout for Pyntara and explains what each directory and file contains.

## Configuration editing

Many tasks must not overwrite whole files; they must perform targeted line-level edits while preserving unrelated content and comments.

Preferred tools and approaches:
Augeas where a format lens exists
comby where no lens exists but structure is regular (most .conf/.ini-like files)
dasel/yq/jq for JSON/YAML/TOML/XML
managed blocks with marker verification as universal fallback

The managed-block fallback must be implemented as a small shared library, not duplicated across scripts.

## Top-level files

inst.sh — Bootstrap installer: installs dependencies, clones repo, launches Python CLI. See docs/contracts/bootstrap.md.
README.md — Quick start, installation modes, and links to detailed docs.
config.toml — Engine configuration and the task catalog, single source of truth for the Python part. See docs/contracts/architecture.md.
.gitignore — Ignore rules for virtualenvs, caches, logs, and runtime task data.

## docs/

contracts/ — Mandatory runtime specifications
spec/ — Functional specification, what the system does
guides/ — How to work with the project

## secrets/

secrets/default.vault — Default/fallback KeePass database for test or recovery scenarios. In git.
secrets/production.vault — Production KeePass database with real secrets. In git.
secrets/default.password — Password for default.vault (well-known test value). In git.
secrets/production.password — Password for production.vault. Not in git (.gitignore).

The layout of both vault files is described in the [vault_structure] table of config.toml, the single source of truth for the vault structure (docs/spec/secrets-model.md).
secrets/regenerate_vault_by_config.py — Creates or updates a vault file from the [vault_structure] table of config.toml (docs/spec/secrets-model.md).

## src/pyntara/

src/pyntara/__init__.py — Package version and public exports.
src/pyntara/pyntara.py — Command entry (check-vault, run) and composition root. The only module that reads the environment.
src/pyntara/config.py — Config.toml loading: Config frozen dataclass, load_config, ConfigError.
src/pyntara/task_catalog.py — Task catalog logic: validate_mode, default_tasks, resolve, unknown_tasks operating on the catalog loaded from config.toml.
src/pyntara/models.py — TaskResult dataclass.
src/pyntara/context.py — Context frozen dataclass.
src/pyntara/task_runner.py — Task execution engine: loads task modules by name, runs them in order, collects results.
src/pyntara/utils.py — Shared helpers: run_command subprocess wrapper with timeout and return-code checks.
src/pyntara/tasks/ — One module per task, each exposing task(ctx) -> TaskResult.

Not implemented yet (target modules, see docs/simplified-architecture.md):
src/pyntara/secrets_store.py — Vault loading/decryption and controlled secret access API.
src/pyntara/config_edit.py — Managed-block config editing helper.
src/pyntara/systemd.py — Creation/update of systemd unit files and timers.
src/pyntara/telemetry.py — Telemetry generation, in-memory PDF encryption, queues, retries, and scheduling.

### src/pyntara/tasks/

One module per task, each exposing task(ctx) -> TaskResult. Task names come from the [[tasks]] section of config.toml, the single source of truth; the module list is not repeated here so renames in the config cannot leave stale names behind.

## task_data/

task_data/.gitkeep — Keeps empty directory in git; runtime task state is stored in per-task subdirectories.

## Notes

This file describes the target structure for the project as implementation work progresses.
Runtime-generated sensitive data must not be committed.
Task implementations must stay idempotent and use explicit dependency passing via Context.
Default datetime format is YYYY-MM-DD-HH-MM-SS unless a compatibility exception is required.
