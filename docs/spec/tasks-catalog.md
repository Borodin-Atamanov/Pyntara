# Tasks catalog

Each task is a separate Python module in src/pyntara/tasks/. Task file name matches task name in configuration.

## Task contract

All tasks implement the protocol from docs/contracts/task-model.md.
Each task is idempotent. Repeated runs must not destroy an already configured system.
Tasks support force mode that reruns a task even after completion.

## Task list

users — Create and configure i, j, k users and required groups. User i is main user, all belong to sudo users.
hostname — Generate and persist random 9-character hostname.
passwords — Derive root/user passwords from salt and hostname. Root: 20 chars, regular user: 16 chars.
zram — Configure aggressive ZRAM by CPU/RAM. Fallback to 8 cores if count cannot be determined. Strong compression, using almost all memory.
swapfile — Calculate and configure swapfile. Size from formulas considering RAM and free disk space.
ssh — Install and configure SSH service. Patch daemon config, add pre-generated certificates for passwordless login.
proxy_server — Local authenticated proxy service with password/port. Runs as Kubuntu system service.
proxy_tunnel — Local tunnel to remote proxy/VPN. Connection parameters from secrets.
ntp — Enable and tune NTP synchronization. Uses large server list from most accurate to least.
power — Configure power behavior. No suspend on lid close, no suspend on user inactivity.
desktop — Desktop defaults: Kate opens new document, terminal starts in /home/i/Downloads with larger font and scrollback, language indicator shows Argentina flag for Spanish, user folders point to /home/i/Downloads, Dolphin sidebar cleanup.
apps — Install latest ImageMagick, FFmpeg, scrcpy. High resource limits, execution stability, widest format support.
nextdns — Per-user NextDNS account via browser automation. Apply DNS endpoint system-wide. Include endpoint in telemetry.
telemetry_setup — Initial telemetry service setup and first-run queue bootstrap. See docs/spec/telemetry.md.

## Task dependencies

Enabling a task auto-enables all its required dependencies transitively.
Disabling a task does not auto-disable dependent tasks.
Task set and metadata are defined in configuration (install_modes.yaml).
