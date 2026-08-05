# Users, host, and system settings

This document specifies system-level parameters for tasks defined in `docs/contracts/task-model.md`.
Task descriptions and dependencies are in the catalog; this document covers only configuration details.

## ZRAM

ZRAM is configured based on CPU core count.
If core count cannot be determined, use 8.

The number of ZRAM devices equals the number of CPU cores.
Each device is sized to the same share of 96 percent of installed RAM, rounded down to the 4096-byte zram page size.
Total ZRAM capacity is 96 percent of installed RAM.

ZRAM should be aggressive, with strong compression, using almost all memory.
Each device uses the zstd compression algorithm.
ZRAM swap is activated with priority 1111, so it is used before the disk swapfile.

The zram_service task configures the devices immediately and installs a systemd oneshot service that repeats the setup at every boot.

## Swap file

Size is calculated using formulas in configuration.
RAM and free disk space are both considered.

These tasks create system services executed at system startup.

## NTP

Use a large server list, starting from the most accurate and reliable.

## Power management

Do not suspend/sleep when lid is closed.
Do not suspend on user inactivity.

## Session restore

Do not restore previous windows at next system start.

## Logs and services

Pyntara creates background services.
Services write logs to proper Linux-standard storage locations.
Logs must be rotated.
Other logs are usually not sent regularly to telemetry and remain local with rotation.
Service logs should be verbose by default (detail levels), with consistent history of actions and command results.
Secrets must not appear in logs in plain form; masking is required.
