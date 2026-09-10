"""[swapfile_service_install] table: swap file parameters."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SwapfileServiceInstallConfig:
    """Swap file parameters for the swapfile_service_install task.

    The swap size is min(RAM * ram_multiplier + ram_extra_mb,
    free_disk * disk_fraction); ram_multiplier and ram_extra_mb size the
    swap from installed RAM, disk_fraction caps it by free disk space.
    swapfile_mode is the octal file mode of the created swapfile;
    size_tolerance_mb is the accepted deviation between the existing and
    the target swap size in mebibytes, so a swapfile resized by rounding
    is not recreated. service_unit_name is the name of the systemd
    oneshot service that activates the swap at boot.
    """

    swapfile_path: Path
    ram_multiplier: float
    ram_extra_mb: int
    disk_fraction: float
    swapfile_mode: int
    size_tolerance_mb: int
    service_unit_name: str
