"""[zswap_service] table: compressed swap cache parameters."""


from __future__ import annotations

from dataclasses import dataclass


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
