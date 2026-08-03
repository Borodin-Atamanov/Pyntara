# Project structure

This document defines the target repository layout for Pyntara and explains what each directory and file contains.

## Top-level files

| Path | Purpose |
|---|---|
| `inst.sh` | Bootstrap installer: installs dependencies, clones repo, launches Python CLI. See `docs/bootstrap-contract.md`. |
| `README.md` | Quick start, installation modes, and links to detailed docs. |
| `.gitignore` | Ignore rules for virtualenvs, caches, logs, and runtime task data. |

### `docs/`

| Path | Purpose |
|---|---|
| `docs/architecture.md` | Runtime architecture baseline and enforced component boundaries. |
| `docs/project-structure.md` | Canonical overview of repository layout and file responsibilities. |
| `docs/project-rules.md` | Project-wide defaults for command output and datetime format. |
| `docs/interactive-ui-contract.md` | Source-of-truth for interactive terminal UX flow, timers, and checkbox semantics. |
| `docs/bootstrap-contract.md` | Source-of-truth for the bootstrap installer `inst.sh`. |

## Directories

### `secrets/`

| Path | Purpose |
|---|---|
| `secrets/default.vault` | Default/fallback KeePass database for test or recovery scenarios. In git. |
| `secrets/production.vault` | Production KeePass database with real secrets. In git. |
| `secrets/default.password` | Password for `default.vault` (well-known test value). In git. |
| `secrets/production.password` | Password for `production.vault`. **Not in git** (`.gitignore`). |

### `src/pyntara/`

| Path | Purpose |
|---|---|
| `src/pyntara/__init__.py` | Package version and public exports. |
| `src/pyntara/cli.py` | Typer commands and composition root that builds immutable `RunContext`. |
| `src/pyntara/context.py` | `RunContext` assembly and dependency injection wiring. |
| `src/pyntara/models.py` | Pydantic configuration models and dataclasses such as `TaskResult`. |
| `src/pyntara/logging_setup.py` | Logger initialization, syslog integration, and secret masking filters. |
| `src/pyntara/secrets_store.py` | Vault loading/decryption and controlled secret access API. |
| `src/pyntara/config_loader.py` | Config merge logic with priority: CLI > env > file > defaults. |
| `src/pyntara/task_protocol.py` | `typing.Protocol` contract for all tasks. |
| `src/pyntara/task_registry.py` | Task registration and metadata lookup by task name. |
| `src/pyntara/task_runner.py` | Task execution engine, status handling, idempotency, and `force` mode. |
| `src/pyntara/config_edit.py` | Targeted config editing adapters (`augeas`, `comby`, structured formats, managed blocks fallback). |
| `src/pyntara/telemetry.py` | Telemetry generation, in-memory PDF encryption, queues, retries, and scheduling. |
| `src/pyntara/systemd.py` | Creation/update of systemd unit files and timers. |
| `src/pyntara/utils.py` | Shared helpers (`subprocess` wrappers with timeout and return-code checks, path/network helpers). |

### `src/pyntara/tasks/`

| Path | Purpose |
|---|---|
| `users.py` | Create and configure `i`, `j`, `k` users and required groups. |
| `hostname.py` | Generate and persist random 9-character hostname. |
| `passwords.py` | Derive root/user passwords from salt and hostname with configured lengths. |
| `zram.py` | Configure aggressive ZRAM by CPU/RAM with fallback behavior. |
| `swapfile.py` | Calculate and configure swapfile from RAM/free-space formulas. |
| `ssh.py` | Install and configure SSH service, including secure config updates. |
| `proxy_server.py` | Local authenticated proxy service setup and management. |
| `proxy_tunnel.py` | Local tunnel to remote proxy/VPN using secrets. |
| `ntp.py` | Enable and tune NTP synchronization from `ntp_servers.txt`. |
| `power.py` | Configure power behavior (no suspend on lid close/idle). |
| `desktop.py` | Desktop defaults (Kate, terminal profile, folders/sidebar behavior). |
| `apps.py` | Installation/configuration of ImageMagick, FFmpeg, and scrcpy. |
| `nextdns.py` | NextDNS account/bootstrap integration and system DNS application. |
| `telemetry_setup.py` | Initial telemetry service setup and first-run queue bootstrap. |

### `task_data/`

| Path | Purpose |
|---|---|
| `task_data/.gitkeep` | Keeps empty directory in git; runtime task state is stored in per-task subdirectories. |

## Notes

- This file describes the **target** structure for the project as implementation work progresses.
- Runtime-generated sensitive data must not be committed.
- Task implementations must stay idempotent and use explicit dependency passing via `RunContext`.
- Default datetime format is `YYYY-MM-DD-HH-MM-SS` unless a compatibility exception is required.
