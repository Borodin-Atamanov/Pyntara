"""[zswap_service] table: compressed swap cache parameters."""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import ConfigError, _int_field, _nonempty_string_field


@dataclass(frozen=True)
class ZswapServiceConfig:
    """Compressed swap cache parameters for the zswap_service task.

    The values are written into /sys/module/zswap/parameters. enabled and
    shrinker_enabled are strict booleans; compressor names the compression
    algorithm; max_pool_percent and accept_threshold_percent are the pool
    ceiling and the re-accept threshold as percentages of RAM and of the
    pool limit. service_unit_name is the name of the systemd oneshot
    service that repeats the writes at boot.
    """

    enabled: bool
    compressor: str
    max_pool_percent: int
    accept_threshold_percent: int
    shrinker_enabled: bool
    service_unit_name: str


def _zswap_service_table(raw: object) -> ZswapServiceConfig:
    """Validate the [zswap_service] table and build ZswapServiceConfig.

    enabled and shrinker_enabled are strict booleans; compressor is a
    non-empty string; max_pool_percent and accept_threshold_percent are
    integers between 1 and 100, the meaningful range for a percentage that
    the kernel accepts on the sysfs attributes. A pool ceiling of zero
    would disable zswap entirely, so it is rejected here.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[zswap_service] section is missing or not a table")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError("zswap_service.enabled must be a boolean")
    compressor = raw.get("compressor")
    if not isinstance(compressor, str) or not compressor:
        raise ConfigError("zswap_service.compressor must be a non-empty string")
    max_pool_percent = _int_field(
        raw.get("max_pool_percent"), "zswap_service.max_pool_percent"
    )
    if not 1 <= max_pool_percent <= 100:
        raise ConfigError(
            "zswap_service.max_pool_percent must be between 1 and 100"
        )
    accept_threshold_percent = _int_field(
        raw.get("accept_threshold_percent"),
        "zswap_service.accept_threshold_percent",
    )
    if not 1 <= accept_threshold_percent <= 100:
        raise ConfigError(
            "zswap_service.accept_threshold_percent must be between 1 and 100"
        )
    shrinker_enabled = raw.get("shrinker_enabled")
    if not isinstance(shrinker_enabled, bool):
        raise ConfigError("zswap_service.shrinker_enabled must be a boolean")
    return ZswapServiceConfig(
        enabled=enabled,
        compressor=compressor,
        max_pool_percent=max_pool_percent,
        accept_threshold_percent=accept_threshold_percent,
        shrinker_enabled=shrinker_enabled,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "zswap_service.service_unit_name"
        ),
    )
