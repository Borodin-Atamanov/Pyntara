"""Values of the swapfile_service_install task.

The section deploys one program and the boot service that runs it. The program
creates the swap file at the size min(RAM * ram_multiplier + ram_extra_mb,
free_disk * disk_fraction), formats it, activates it, and refuses storage that
keeps its data in memory, so the size formula and the commands of the swap
itself live in the program alone and the task carries only what it must pass and
where to put things (docs/spec/users-and-host.md).

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

# Size of the file the program tries the storage with, in kibibytes. The probe
# is created next to the swap file and removed again, so storage that keeps its
# data in memory costs a few kibibytes instead of the size of the swap file.
PROBE_SIZE_KB: int = 512

# The kernel file the installed memory is read from, handed to the program.
MEMINFO_PATH: Path = Path("/proc/meminfo")

# The program of the section: the file of the clone under task_data, the path it
# is deployed to and the mode that makes it executable. The name carries the
# project, because the deployed directory holds the commands of several
# sections.
PROGRAM_FILE_NAME: str = "configure_swapfile.py"
PROGRAM_DEPLOY_PATH: Path = Path("/usr/local/bin/pyntara-swapfile")
PROGRAM_FILE_MODE: int = 0o755

# Name of the systemd oneshot service unit that runs the program at boot.
SERVICE_UNIT_NAME: str = "swapfile.service"

# Name of the unit template under task_data/swapfile_service_install/ of the
# clone; the run renders it with the command line of the program and the
# swapfile path.
UNIT_TEMPLATE_FILE_NAME: str = "swapfile.service"

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
    "PROBE_SIZE_KB",
    "MEMINFO_PATH",
    "PROGRAM_FILE_NAME",
    "PROGRAM_DEPLOY_PATH",
    "PROGRAM_FILE_MODE",
    "SERVICE_UNIT_NAME",
    "UNIT_TEMPLATE_FILE_NAME",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
)
