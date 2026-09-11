"""[zswap_service] table: compressed swap cache parameters."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ZswapServiceConfig:
    """Compressed swap cache parameters for the zswap_service task.

    The values are written into the kernel attribute directory
    parameters_dir_path, one file per name in parameter_names, in the order
    the list gives. The value of a parameter is the key of this section with
    the same name, so enabled and shrinker_enabled are strict booleans
    written as Y or N, compressor names the compression algorithm and
    max_pool_percent and accept_threshold_percent are the pool ceiling and
    the re-accept threshold as percentages of RAM and of the pool limit.
    unit_template_file_name is the oneshot unit template under
    task_data/zswap_service/ of the clone and service_unit_name the name of
    the systemd oneshot service that repeats the writes at boot.
    """

    enabled: bool
    compressor: str
    max_pool_percent: int
    accept_threshold_percent: int
    shrinker_enabled: bool
    parameters_dir_path: Path
    parameter_names: tuple[str, ...]
    unit_template_file_name: str
    service_unit_name: str
