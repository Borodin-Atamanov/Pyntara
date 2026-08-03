# Users, host, and system settings

## Users

Create user i (main user).
User i must belong to groups sudo users.
Also create additional users j and k, also in sudo users.
Generate password for root.

## Hostname

Dedicated task: generate computer name (random, 9 characters).

## ZRAM

Dedicated task: install and configure ZRAM.
ZRAM is configured based on CPU core count.
If core count cannot be determined, use 8.
ZRAM should be aggressive, with strong compression, using almost all memory.

## Swap file

Dedicated task: create/configure swap file.
Size is calculated using formulas in configuration.
RAM and free disk space are both considered.

These tasks create system services executed at system startup.

## NTP

Dedicated task: automatic time sync with NTP servers.
Use a large server list, starting from the most accurate and reliable.

## Power management

Dedicated task: power management modes.
Do not suspend/sleep when lid is closed.
Do not suspend on user inactivity.

## Session restore

Dedicated task: do not restore previous windows at next system start.

## Logs and services

Pyntara creates background services.
Services write logs to proper Linux-standard storage locations.
Logs must be rotated.
Other logs are usually not sent regularly to telemetry and remain local with rotation.
Service logs should be verbose by default (detail levels), with consistent history of actions and command results.
Secrets must not appear in logs in plain form; masking is required.
