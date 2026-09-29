"""Values of the zram_service task.

The section configures aggressive in-memory swap and deploys the program that
keeps it configured at every boot. The device count equals the CPU core count,
the total capacity is a fraction of installed RAM split evenly across the
devices and rounded down to the byte boundary the zram driver requires, and
every device uses the chosen compressor and is activated with the swap
priority, so ZRAM swap ranks above the disk swapfile
(docs/spec/users-and-host.md).

The section deploys one program and the boot service that runs it
(task_data/zram_service/configure_zram.py). The program reads the installed
memory and the core count at every run, so a machine restarted with another
memory size or another core count is configured for what it really has; the
size formula and the commands of the zram devices live in the program alone and
this module carries only what the task must pass, where to put things and which
packages its tools come from.

The kernel file the installed memory is read from and the name of the line that
carries the total come from the shared module, because the swapfile section
reads the same file and line.
"""

from __future__ import annotations

from pathlib import Path

# Compression algorithm applied to every device of this section.
COMPRESSOR: str = "zstd"

# Swap priority; ZRAM must rank above the disk swapfile.
SWAP_PRIORITY: int = 1111

# Total ZRAM capacity as a percentage of installed RAM, 1 to 100.
MEMORY_FRACTION_PERCENT: int = 96

# CPU core count used when the real count cannot be determined.
FALLBACK_CPU_COUNT: int = 8

# Name of the per-core line of /proc/cpuinfo. A kernel that renames the field is
# answered here.
CPUINFO_PROCESSOR_KEY: str = "processor"

# The kernel file the core count is read from, handed to the program.
CPUINFO_PATH: Path = Path("/proc/cpuinfo")

# Byte boundary the zram driver requires for disksize values.
ALIGNMENT_BYTES: int = 4096

# Retries of a reset or hot_remove rejected with EBUSY while a transient opener,
# for example a udev probe, holds the device, and the pause between two attempts.
RESET_BUSY_ATTEMPTS: int = 5
RESET_BUSY_RETRY_DELAY_SECONDS: float = 0.5

# Name of the kernel module that provides the compressed devices.
MODULE_NAME: str = "zram"

# The packages the tools of the program come from: swapon and swapoff are in
# mount, mkswap in util-linux and modprobe in kmod. The task installs them
# through the shared package helper, so the section never assumes the machine
# already carries them.
PACKAGES: tuple[str, ...] = ("mount", "util-linux", "kmod")

# The program of the section: the file of the clone under task_data, the path it
# is deployed to and the mode that makes it executable. The name carries the
# project, because the deployed directory holds the commands of several
# sections.
PROGRAM_FILE_NAME: str = "configure_zram.py"
PROGRAM_DEPLOY_PATH: Path = Path("/usr/local/bin/pyntara-zram")
PROGRAM_FILE_MODE: int = 0o755

# Name of the systemd oneshot service unit that runs the program at boot.
SERVICE_UNIT_NAME: str = "zram.service"

# Name of the unit template under task_data/zram_service/ of the clone; the run
# renders it with the command line of the program.
UNIT_TEMPLATE_FILE_NAME: str = "zram.service"

# systemctl calls of the task, each carrying the unit name as its placeholder.
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)
SYSTEMCTL_START_COMMAND: tuple[str, ...] = (
    "systemctl",
    "start",
    "{service_unit_name}",
)

# The names the task reads. The list lives next to the values it names and is
# read by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "COMPRESSOR",
    "SWAP_PRIORITY",
    "MEMORY_FRACTION_PERCENT",
    "FALLBACK_CPU_COUNT",
    "CPUINFO_PROCESSOR_KEY",
    "CPUINFO_PATH",
    "ALIGNMENT_BYTES",
    "RESET_BUSY_ATTEMPTS",
    "RESET_BUSY_RETRY_DELAY_SECONDS",
    "MODULE_NAME",
    "PACKAGES",
    "PROGRAM_FILE_NAME",
    "PROGRAM_DEPLOY_PATH",
    "PROGRAM_FILE_MODE",
    "SERVICE_UNIT_NAME",
    "UNIT_TEMPLATE_FILE_NAME",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
    "SYSTEMCTL_START_COMMAND",
)
