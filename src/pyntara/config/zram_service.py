"""[zram_service] table: in-memory swap parameters."""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import (
    ConfigError,
    _float_field,
    _int_field,
    _nonempty_string_field,
)


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


def _zram_service_table(raw: object) -> ZramServiceConfig:
    """Validate the [zram_service] table and build ZramServiceConfig.

    compressor is a non-empty string; swap_priority is a positive swap
    priority; memory_fraction_percent is a percentage between 1 and 100;
    fallback_cpu_count is at least 1; alignment_bytes is positive, because
    the zram driver rejects a non-positive or unaligned disksize;
    reset_busy_attempts is at least 1 and
    reset_busy_retry_delay_seconds is positive.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[zram_service] section is missing or not a table")
    compressor = raw.get("compressor")
    if not isinstance(compressor, str) or not compressor:
        raise ConfigError("zram_service.compressor must be a non-empty string")
    swap_priority = _int_field(
        raw.get("swap_priority"), "zram_service.swap_priority"
    )
    if swap_priority < 1:
        raise ConfigError("zram_service.swap_priority must be positive")
    memory_fraction_percent = _int_field(
        raw.get("memory_fraction_percent"),
        "zram_service.memory_fraction_percent",
    )
    if not 1 <= memory_fraction_percent <= 100:
        raise ConfigError(
            "zram_service.memory_fraction_percent must be between 1 and 100"
        )
    fallback_cpu_count = _int_field(
        raw.get("fallback_cpu_count"), "zram_service.fallback_cpu_count"
    )
    if fallback_cpu_count < 1:
        raise ConfigError("zram_service.fallback_cpu_count must be at least 1")
    alignment_bytes = _int_field(
        raw.get("alignment_bytes"), "zram_service.alignment_bytes"
    )
    if alignment_bytes < 1:
        raise ConfigError("zram_service.alignment_bytes must be positive")
    reset_busy_attempts = _int_field(
        raw.get("reset_busy_attempts"), "zram_service.reset_busy_attempts"
    )
    if reset_busy_attempts < 1:
        raise ConfigError(
            "zram_service.reset_busy_attempts must be at least 1"
        )
    reset_busy_retry_delay_seconds = _float_field(
        raw.get("reset_busy_retry_delay_seconds"),
        "zram_service.reset_busy_retry_delay_seconds",
    )
    if reset_busy_retry_delay_seconds <= 0:
        raise ConfigError(
            "zram_service.reset_busy_retry_delay_seconds must be positive"
        )
    return ZramServiceConfig(
        compressor=compressor,
        swap_priority=swap_priority,
        memory_fraction_percent=memory_fraction_percent,
        fallback_cpu_count=fallback_cpu_count,
        alignment_bytes=alignment_bytes,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "zram_service.service_unit_name"
        ),
        reset_busy_attempts=reset_busy_attempts,
        reset_busy_retry_delay_seconds=reset_busy_retry_delay_seconds,
    )
