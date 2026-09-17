"""Values of the swapfile_service_install task.

The section sizes and installs the swap file of the machine: the size is
min(RAM * ram_multiplier + ram_extra_mb, free_disk * disk_fraction), and the
commands that allocate, activate and format the file are values here as well
(docs/spec/swap-and-memory.md).

The name of the /proc/meminfo line that carries the installed RAM is read from
the shared module, because the zram_service section reads the same line.
"""

from __future__ import annotations

from pathlib import Path

# Path to the swap file.
SWAPFILE_PATH: Path = Path("/swapfile")

# Multiplier applied to installed RAM to get the base swap size.
RAM_MULTIPLIER: float = 1.6

# Extra mebibytes added to the RAM-derived swap size before the disk cap.
RAM_EXTRA_MB: int = 4096

# Maximum share of free disk space the swap file may occupy, 0.0 to 1.0.
DISK_FRACTION: float = 0.5

# File mode of the created swapfile.
SWAPFILE_MODE: int = 0o600

# Accepted deviation between the existing and the target swap size, in
# mebibytes; a swapfile within the tolerance is not recreated.
SIZE_TOLERANCE_MB: int = 1

# Name of the systemd oneshot service unit that activates the swap at boot.
SERVICE_UNIT_NAME: str = "swapfile.service"

# Name of the unit template under task_data/swapfile_service_install/ of the
# clone; the run renders it with the swapfile path.
UNIT_TEMPLATE_FILE_NAME: str = "swapfile.service"

# Command that lists the active swap devices; the swapfile path found in its
# output is the active swap.
SWAP_SHOW_COMMAND: tuple[str, ...] = ("swapon", "--show", "--noheadings")

# Commands that activate and deactivate the swapfile; {swapfile_path} is the
# configured swap file.
SWAP_ON_COMMAND: tuple[str, ...] = ("swapon", "{swapfile_path}")
SWAP_OFF_COMMAND: tuple[str, ...] = ("swapoff", "{swapfile_path}")

# Command that allocates the swapfile at the computed size; {size_mb} is the
# target size in mebibytes.
CREATE_COMMAND: tuple[str, ...] = (
    "fallocate",
    "-l",
    "{size_mb}M",
    "{swapfile_path}",
)

# Command that applies the configured file mode; {file_mode} is the mode of the
# section in the octal form chmod expects.
CHMOD_COMMAND: tuple[str, ...] = ("chmod", "{file_mode}", "{swapfile_path}")

# Command that writes the swap signature into the file.
FORMAT_COMMAND: tuple[str, ...] = ("mkswap", "{swapfile_path}")

# systemctl calls of the task, each carrying the unit name as its placeholder.
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)

# The names the task reads. The list lives next to the values it names and is
# read by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "SWAPFILE_PATH",
    "RAM_MULTIPLIER",
    "RAM_EXTRA_MB",
    "DISK_FRACTION",
    "SWAPFILE_MODE",
    "SIZE_TOLERANCE_MB",
    "SERVICE_UNIT_NAME",
    "UNIT_TEMPLATE_FILE_NAME",
    "SWAP_SHOW_COMMAND",
    "SWAP_ON_COMMAND",
    "SWAP_OFF_COMMAND",
    "CREATE_COMMAND",
    "CHMOD_COMMAND",
    "FORMAT_COMMAND",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
)
