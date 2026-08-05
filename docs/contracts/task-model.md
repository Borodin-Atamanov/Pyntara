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

The catalog lives in config.toml under the [[tasks]] section. Each entry has name, description, dependencies and mode membership; dependencies must name tasks listed earlier in the file.

add_extra_repos — Enable extra Ubuntu archive components: universe, restricted, multiverse. Runs first so package tasks resolve their packages.
users — Create and configure i, j, k users and required groups. User i is main user, all belong to sudo users.
hostname — Generate and persist random 9-character hostname.
passwords — Derive root/user passwords from salt and hostname. Root: 20 chars, regular user: 16 chars.
cli_tools — Install curated console utilities: file managers, system and media tools. Depends on add_extra_repos.
zram — Configure aggressive ZRAM by CPU/RAM. Fallback to 8 cores if count cannot be determined. Strong compression, using almost all memory.
swapfile — Calculate and configure swapfile. Size from formulas considering RAM and free disk space.
ssh — Install and configure SSH service. Patch daemon config, add pre-generated certificates for passwordless login.
proxy_server — Local authenticated proxy service with password/port. Runs as Kubuntu system service.
proxy_tunnel — Local tunnel to remote proxy/VPN. Connection parameters from secrets.
power — Configure power behavior. No suspend on lid close, no suspend on user inactivity.
desktop — Desktop defaults: Kate opens new document, terminal starts in /home/i/Downloads with larger font and scrollback, language indicator shows Argentina flag for Spanish, user folders point to /home/i/Downloads, Dolphin sidebar cleanup.
imagemagick_install — Install latest ImageMagick. High resource limits, execution stability, widest format support. Depends on add_extra_repos.
nextdns — Per-user NextDNS account via browser automation. Apply DNS endpoint system-wide. Include endpoint in telemetry.
telemetry_setup — Initial telemetry service setup and first-run queue bootstrap. See docs/spec/telemetry.md.

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
