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
    oneshot service that activates the swap at boot and
    unit_template_file_name the template of that unit under task_data/ of
    the clone.

    The commands of the tools the task drives are values of this section
    as well: swap_show_command lists the active swap devices,
    swap_on_command and swap_off_command activate and deactivate the
    swapfile ({swapfile_path}), create_command allocates it at the target
    size ({size_mb} in mebibytes), chmod_command applies swapfile_mode in
    the octal form chmod expects ({file_mode}), format_command writes the
    swap signature, and systemctl_daemon_reload_command and
    systemctl_enable_command are the two systemctl calls of the run
    (the second with {service_unit_name}). One shared helper fills the
    placeholders.
    """

    swapfile_path: Path
    ram_multiplier: float
    ram_extra_mb: int
    disk_fraction: float
    swapfile_mode: int
    size_tolerance_mb: int
    service_unit_name: str
    unit_template_file_name: str
    swap_show_command: tuple[str, ...]
    swap_on_command: tuple[str, ...]
    swap_off_command: tuple[str, ...]
    create_command: tuple[str, ...]
    chmod_command: tuple[str, ...]
    format_command: tuple[str, ...]
    systemctl_daemon_reload_command: tuple[str, ...]
    systemctl_enable_command: tuple[str, ...]
