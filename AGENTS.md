# Pyntara — Technical Specification

## Mandatory rules for all agents
- You always refer to yourself in the feminine gender and to me in the masculine, addressing me obsequiously, using the formal "Вы"
- Before any repository action, the agent MUST read `AGENTS.md` in full.
- After finishing changes, the agent MUST integrate them into `main` immediately.
- Before commit, the agent MUST run the full test suite and fix all failures until green.
- Testing MUST be deep and cover both the Python application and the bootstrap installer `inst.sh`.

## Documentation index (link-only)
- Runtime architecture contract: `docs/architecture.md`
- Repository layout contract: `docs/project-structure.md`
- Project-wide defaults: `docs/project-rules.md`
- Interactive terminal UX contract: `docs/interactive-ui-contract.md`
- Bootstrap installer contract: `docs/bootstrap-contract.md`

## Project purpose and context
- Pyntara is an automated Kubuntu provisioning system.
- Primary target platform: Kubuntu 26.04.
- Architecturally, the system should also work on other versions.

## Startup and initial bootstrap
- The system starts via `inst.sh` (a regular Bash script).
- The script is downloaded from GitHub and run as superuser.
- The only startup check: the script must be running as root. No OS or distribution checks.
- The script installs packages using optimistic apt strategy: try `apt-get install` first, run `apt-get update` only if packages are missing, then retry.
- Install order: `dialog` first, then `python3`, `python3-venv`, `git`, `curl`, `ca-certificates`, then `uv`.
- User-level utilities (`htop`, `nload`, `wget`, and similar) are installed by separate tasks, not by the bootstrap.
- Source delivery: `git clone --depth 1` only. On repeated runs, `git fetch` + reset in the existing directory instead of re-cloning.
- All commands run in maximum verbosity, non-interactive mode. Significant commands are wrapped in `time`.
- After cloning, `uv sync` prepares the Python environment and `uv run pyntara` starts the CLI.
- There is no timeout on the Pyntara process. It runs as long as needed.
- All interactive screens use `dialog`. See `docs/interactive-ui-contract.md`.
- Every function in `inst.sh` is declared with a guard (`if ! declare -f name ...`) so tests can substitute any function via `source`.
- Full contract: `docs/bootstrap-contract.md`.

## Installation modes and task selection
- The user is offered 3 installation options:
  - minimal,
  - server,
  - desktop.
- Environment auto-detection is used:
  - on desktop systems, desktop mode is selected by default,
  - on server systems, server mode is selected by default.
- Auto-selection timeout: 11 seconds.
- The timeout is shown in terminal and decreases in real time.
- If the user does not press a key (up/down/right/left or Enter), the default option is selected.
- After mode selection, a task list is shown as checkboxes.
- Each task has:
  - explicit ordering,
  - name,
  - human-readable description.
- Task set and metadata are defined in configuration.

## Secrets and passwords
- The repository contains two KeePass vault files: `default.vault` and `production.vault`. Both are in git.
- Two password files: `default.password` (in git, well-known test value) and `production.password` (in `.gitignore`, must never be committed).
- KeePass database handling is done via a Python library.
- Password prompt is the first interactive screen (before mode selector).
- The user gets 3 attempts to enter the production vault password via `dialog --passwordbox` (11s timeout per attempt).
- After 3 failed attempts, the system falls back to `default.vault` using `default.password`.
- If user does not press any key within 11 seconds, fallback to `default.vault` immediately.
- With a correct password:
  - the database is decrypted,
  - some values become environment variables,
  - some values are saved into internal machine configuration,
  - some values are one-time-use and must only live in memory during execution.
- The system uses salts:
  - default salt from GitHub,
  - salt from KeePass, which overrides the default when present.
- Salt replacement must be reflected in logs.
- Passwords are generated from salt + random hostname for:
  - `root`,
  - user `i`,
  - additional users `j` and `k`.
- Default password lengths:
  - `root`: 20 characters,
  - regular user: 16 characters.

## Task model and idempotency
- Checkbox-selected tasks are not only binary; they must have at least three states.
- Each task must be idempotent:
  - repeated runs must not destroy an already configured system.
- If target state is already reached, a task normally skips changes.
- Tasks must support `force` mode that reruns a task even after completion.
- Per-task configuration must define:
  - what the task does,
  - which file executes it.
- Preferred structure:
  - each task in a separate file,
  - dedicated tasks directory.
- Task file name must match task name in configuration.
- Task data is stored in a shared task-data directory, in a subdirectory matching task name.
- Tasks may have their own configuration, also stored in the task data folder.
- Example of a meaningful task: install and configure SSH server, patch daemon config, add pre-generated certificates for passwordless login.

## Configuration editing
- Many tasks must not overwrite whole files; they must perform targeted line-level edits while preserving unrelated content and comments.
- Preferred tools/approaches:
  - `Augeas` where a format lens exists,
  - `comby` where no lens exists but structure is regular (most `.conf`/`.ini`-like files),
  - `dasel`/`yq`/`jq` for JSON/YAML/TOML/XML,
  - managed blocks with marker verification as universal fallback.
- The managed-block fallback must be implemented as a small shared library, not duplicated across scripts.
- Migration to `chezmoi` is planned.

## Telemetry
- There is a dedicated telemetry installation task.
- At system start, network availability is checked.
- If network is unavailable, retry interval increases by `sqrt(2)` each attempt (e.g., 1.0 s, 1.4 s, ...).
- When network appears, telemetry attempts to send data.
- Delivery channels and endpoints come from secrets:
  - Telegram bot (messages and files),
  - Google Drive (file uploads).
- Telemetry is generated as encrypted PDF files.
- Encryption: AES-256.
- PDF encryption password is generated during Pyntara initialization from:
  - KeePass salt (decrypted with admin password during installation),
  - hostname.
- Hostname is generated randomly: 9 characters (as one of the tasks).
- Telemetry attempts to send immediately after computer boot.
- Base accumulation/retry behavior:
  - telemetry accumulates for one day,
  - after successful send, next send is scheduled for 12:00 local time,
  - if unsent files exist, retries continue with `sqrt(2)` interval growth,
  - retries with this scheme run only if more than one day has passed since last send.
- There are two independent send queues; architecture must allow adding more:
  - Telegram queue,
  - Google Drive queue.
- Unencrypted PDF versions must never be saved to disk (in-memory generation only).
- After send, telemetry files are saved in a dedicated folder.
- Telemetry additionally includes:
  - clipboard text (inside encrypted PDF),
  - startup network information: attempts to detect addresses/channels (Cloudflare, Yggdrasil, IPv6, etc.), machine’s own addresses, and connection availability status.

## Network features, proxy, and access
- Dedicated task: run a local proxy server on the computer with authentication (password/port).
- This proxy runs as a Kubuntu system service and is managed by standard system tools.
- Dedicated task: local proxy tunnel to a remote proxy/VPN.
- Remote proxy connection parameters are taken from secrets unlocked by admin password at first Pyntara installation.
- A local proxy port must be created so any applications can connect to it.

## Users, host, and system settings
- Create user `i` (main user).
- User `i` must belong to groups `sudo users`.
- Also create additional users `j` and `k`, also in `sudo users`.
- Generate password for `root`.
- Dedicated task: generate computer name (random, 9 characters).
- Dedicated task: install and configure ZRAM.
  - ZRAM is configured based on CPU core count.
  - If core count cannot be determined, use 8.
  - ZRAM should be aggressive, with strong compression, using almost all memory.
- Dedicated task: create/configure swap file.
  - Size is calculated using formulas in configuration,
  - RAM and free disk space are both considered.
- These tasks create system services executed at system startup.

- Dedicated task: automatic time sync with NTP servers.
  - Use a large server list, starting from the most accurate and reliable.
- Dedicated task: power management modes.
  - Do not suspend/sleep when lid is closed.
  - Do not suspend on user inactivity.
- Dedicated task: do not restore previous windows at next system start.

## Applications, GUI, and workspace
- Dedicated tasks:
  - install latest `ImageMagick` (possibly from source),
  - install latest `FFmpeg` (possibly from source),
  - install latest `scrcpy` with all capabilities enabled.
- For `ImageMagick` and `FFmpeg`, provide practical local-machine settings:
  - rationally high resource limits,
  - prioritize execution stability (hard swap is better than OOM crash),
  - widest possible format support.
- Kate editor setting: open a new document by default instead of startup screen.
- Terminal settings:
  - start path: `/home/i/Downloads`,
  - larger font size,
  - large scrollback history.
- Language indicator:
  - show country flag instead of text,
  - use Argentina flag for Spanish.
- User folders tasks:
  - default folder: `/home/i/Downloads`,
  - folders such as `Home i Desktop`, `Home i Documents`, `Home i Images`, and other unnecessary ones should point to `/home/i/Downloads` (symlink/hardlink is not critical),
  - separate task to remove these extra folders/links from Dolphin sidebar, leaving only `/home/i/Downloads`.
- Browser workflows (Firefox/Chrome/Chromium):
  - launch with separate profiles,
  - generate a dedicated JSON,
  - use enterprise policy mechanisms to install required extensions and migrate extension/browser settings,
  - launch browsers in a mode suitable for managing AI agents in a visible user window,
  - goal: transparent cookie transfer from user browsers to AI-managed browsers.
- Dedicated task: per-user NextDNS account setup.
  - Account is created through browser automation,
  - unique DNS endpoint is obtained,
  - this endpoint is applied system-wide so DNS requests go through these DNS servers,
  - generated endpoint is included in telemetry,
  - NextDNS keeps query logs and supports filtering.

## Logs and services
- Pyntara creates background services.
- Services write logs to proper Linux-standard storage locations.
- Logs must be rotated.
- Installation log (full install + messages) is sent to telemetry as a separate file.
- Other logs are usually not sent regularly to telemetry and remain local with rotation.
- Service logs should be verbose by default (detail levels), with consistent history of actions and command results.
- Secrets must not appear in logs in plain form; masking is required.

## Architecture and coding standards

### Data flow between components
- Module-level globals for application state are forbidden (configuration, credentials, task results).
- Single state assembly point: composition root in Typer CLI command.
- Configuration is assembled from:
  - file,
  - environment variables,
  - CLI flags.
- Normalize into one object via Pydantic model.
- Source priority: CLI flag > env > file > built-in default.
- Result is one immutable `RunContext` containing:
  - resolved configuration,
  - secrets store,
  - other cross-cutting dependencies.
- `RunContext` is passed explicitly through calls.
- Implicit state reads from `os.environ` (except dedicated components), module-level variables, and other hidden sources are forbidden.

### Task contract
- A task is implemented as a function that accepts `RunContext` and optional typed parameters.
- Task return type: `TaskResult` (dataclass) with fields:
  - success,
  - changes made,
  - error text (if any).
- Data transfer between tasks is explicit only:
  - through API of objects inside `RunContext` (e.g., secrets store),
  - or through orchestrator passing required values as arguments to the next task.
- Hidden data exchange via shared mutable state outside `RunContext` and outside arguments is forbidden.

### Typing and architecture patterns
- For task contract use `typing.Protocol` (structural typing), not mandatory ABC inheritance.
- Stateful classes are allowed where encapsulation is truly needed (example: telemetry delivery client).
- Such classes are created once at entrypoint and passed via `RunContext`, not recreated inside tasks (dependency injection).
- Using stdlib `logging` with module-named logger and `SysLogHandler` to system journal is an allowed exception to shared-state restrictions, because this is infrastructure layer, not business logic.
- The only allowed shared state outside one process memory is explicit external channels:
  - encrypted secrets storage file,
  - telemetry file queue,
  - named IPC command channels.
- Exchange boundaries between processes of different systemd services must be explicitly documented as an architecture contract.

### General engineering requirements
- Full type annotations for all arguments and return values are mandatory.
- Type checking: `mypy --strict`, zero errors.
- Formatting and static analysis: `ruff`, zero warnings before merge.
- `subprocess` calls:
  - no `shell=True`,
  - mandatory return-code checking.
- All setup tasks must be idempotent.
- Re-runs must not break the system and must not overwrite already generated secrets.
- Plaintext secret storage is forbidden (including code and logs).
- External inputs (including config) are validated via Pydantic.
- Internal structures without validation need use dataclass.
- All package-install operations and other operations must have timeouts.
- Tasks must also have reasonable large timeouts configured.
- All processes started from Python must provide return code used for correctness control.

## Testing and CI rules
- Every module with task logic must have pytest unit tests.
- In unit tests, all external resources (`subprocess`, filesystem, network) are mocked via `monkeypatch`.
- For file logic, use `tmp_path`, not real paths.
- Minimum required per task:
  - 1 success scenario test,
  - 1 realistic error scenario test (e.g., command unavailable or permission denied).
- Secrets store must have a test proving that reloading an existing store returns the same values (no regeneration).
- Project must enforce `ruff`, `mypy --strict`, and full `pytest`; pushing to repository without these checks is not allowed.

## Documentation/comment style requirements
- When creating code and configurations, add comments in simple English.
- Comments must explain:
  - what the code does,
  - what each configuration line does,
  - why the action is performed,
  - why the architecture was chosen.
- Explanations must be detailed enough for both humans and machines.
- One consistent formatting/style standard is required across the project.

## Source delivery (resolved)
- Decision: `git clone --depth 1` is the only supported delivery method.
- On repeated runs, `git fetch` + reset in the existing directory instead of re-cloning.
- No archive downloads, no USB fallback, no local source copy.

