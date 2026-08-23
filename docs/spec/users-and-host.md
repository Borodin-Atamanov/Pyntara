# Users, host, and system settings

This document specifies system-level parameters for tasks defined in `docs/contracts/task-model.md`.
Task descriptions and dependencies are in the catalog; this document covers only configuration details.

## Hostname

Task: hostname. The machine hostname is a random proquint word pair: four random bytes encoded by the shared proquint_encode helper into two five-letter words joined by a dash, for example lusab-babad. The randomness comes from the secrets module, so the name is cryptographically strong: the hostname feeds password generation (docs/spec/secrets-model.md) and the deterministic NextDNS profile choice (docs/spec/nextdns-profile.md), so it must not be guessable.

The task writes the name into the configured hostname.hostname_file and applies it to the running kernel through the configured hostname.set_hostname_command, so socket.gethostname() returns the new name for the dependent tasks. The task is idempotent: it skips when the hostname file already carries a name that decodes as a proquint (so it was set by this task) and the kernel already knows it; force mode always generates a fresh name.

## ZRAM

ZRAM is configured based on CPU core count.
The device count equals the number of CPU cores; when the count cannot be determined, fallback_cpu_count is used.

Each device is sized to the same share of memory_fraction_percent of installed RAM, rounded down to the alignment_bytes zram page size.
Total ZRAM capacity is memory_fraction_percent of installed RAM.

ZRAM should be aggressive, with strong compression, using almost all memory.
Each device uses the configured compressor algorithm.
ZRAM swap is activated with the configured swap_priority, so it is used before the disk swapfile.

All parameter values live in the [zram_service] table of the config/ directory: compressor, swap_priority, memory_fraction_percent, fallback_cpu_count, alignment_bytes, reset_busy_attempts and reset_busy_retry_delay_seconds.
reset_busy_attempts and reset_busy_retry_delay_seconds bound the retries of a reset or hot_remove that the kernel rejects with EBUSY while a transient opener, for example a udev probe, holds the device.

The zram_service task configures the devices immediately and installs a systemd oneshot service that repeats the setup at every boot.

## Zswap

Zswap is a compressed cache for swap pages: pages that are being swapped out are compressed into a RAM pool before they reach the backing swapfile, trading CPU cycles for reduced swap I/O.
The zswap_service task writes the parameters into /sys/module/zswap/parameters immediately and installs a systemd oneshot service that repeats the writes at every boot.
Zswap requires a backing swap device, so the task depends on swapfile_service_install.

The values are aggressive, matching the ZRAM philosophy. All parameters live in the [zswap_service] table of the config/ directory: compressor, max_pool_percent, accept_threshold_percent, shrinker_enabled and the service unit name.

## Swap file

Size is calculated using formulas in configuration.
RAM and free disk space are both considered.

These tasks create system services executed at system startup.


