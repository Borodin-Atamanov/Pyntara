"""[zram_service] table: in-memory swap parameters."""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZramServiceConfig:
    """Aggressive in-memory swap parameters for the zram_service task.

    The device count equals the CPU core count (fallback_cpu_count when it
    cannot be determined); the total capacity is memory_fraction_percent of
    installed RAM split evenly across the devices and rounded down to the
    alignment_bytes zram page size. Every device uses the compressor
    algorithm and is activated with swap_priority, so ZRAM swap is
    preferred over the disk swapfile. service_unit_name is the name of the
    systemd oneshot service that repeats the setup at boot.
    reset_busy_attempts and reset_busy_retry_delay_seconds bound the
    retries of a reset or hot_remove rejected with EBUSY while a
    transient opener, for example a udev probe, holds the device.
    """

    compressor: str
    swap_priority: int
    memory_fraction_percent: int
    fallback_cpu_count: int
    alignment_bytes: int
    service_unit_name: str
    reset_busy_attempts: int
    reset_busy_retry_delay_seconds: float
