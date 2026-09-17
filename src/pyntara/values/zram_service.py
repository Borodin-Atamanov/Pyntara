"""Values of the zram_service task.

The section describes the aggressive in-memory swap of the machine: the device
count equals the CPU core count, the total capacity is a fraction of installed
RAM split evenly across the devices and rounded down to the byte boundary the
zram driver requires, and every device uses the chosen compressor and is
activated with the swap priority, so ZRAM swap ranks above the disk swapfile
(docs/spec/users-and-host.md).

The name of the /proc/meminfo line that carries the installed RAM comes from the
shared module, because the swapfile section reads the same line.
"""

from __future__ import annotations

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

# Byte boundary the zram driver requires for disksize values.
ALIGNMENT_BYTES: int = 4096

# Retries of a reset or hot_remove rejected with EBUSY while a transient opener,
# for example a udev probe, holds the device, and the pause between two attempts.
RESET_BUSY_ATTEMPTS: int = 5
RESET_BUSY_RETRY_DELAY_SECONDS: float = 0.5

# Name of the systemd oneshot service unit that repeats the setup at boot, and
# the name of its template under task_data/zram_service/ of the clone.
SERVICE_UNIT_NAME: str = "zram.service"
UNIT_TEMPLATE_FILE_NAME: str = "zram.service"

# Mode bit that marks the hot_add attribute as readable: a readable attribute is
# the kernel 7.0 read-to-add interface, a write-only one is the older
# write-to-add interface.
HOT_ADD_READABLE_MODE_BIT: int = 0o400

# Name of the kernel module that provides the compressed devices; the task loads
# it and the boot service repeats the load.
MODULE_NAME: str = "zram"

# Commands of the run, each carrying what it acts on as its placeholder.
SWAP_SHOW_COMMAND: tuple[str, ...] = ("swapon", "--show", "--noheadings")
MODULE_LOAD_COMMAND: tuple[str, ...] = ("modprobe", "{module_name}")
SWAP_OFF_COMMAND: tuple[str, ...] = ("swapoff", "{device_path}")
FORMAT_COMMAND: tuple[str, ...] = ("mkswap", "{device_path}")
SWAP_ON_COMMAND: tuple[str, ...] = (
    "swapon",
    "--priority",
    "{swap_priority}",
    "{device_path}",
)
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)

# Lines of the ExecStart block that the rendered unit carries: the boot service
# repeats the install-time setup, so every line shape is a value of this section.
# The programs, the systemd directive and the redirections belong to the line
# text; {module_name}, {hot_add_path}, {algorithm_attribute}, {disksize_attribute},
# {size_bytes}, {device_path} and {swap_priority} are filled in by the run.
UNIT_LOAD_LINE: str = "ExecStart=/bin/sh -c 'modprobe {module_name} || true'"
UNIT_ADD_READ_LINE: str = "ExecStart=/bin/cat {hot_add_path}"
UNIT_ADD_WRITE_LINE: str = "ExecStart=/bin/sh -c 'echo 1 > {hot_add_path}'"
UNIT_ALGORITHM_LINE: str = (
    "ExecStart=/bin/sh -c 'echo {compressor} > {algorithm_attribute}'"
)
UNIT_DISKSIZE_LINE: str = (
    "ExecStart=/bin/sh -c 'echo {size_bytes} > {disksize_attribute}'"
)
UNIT_FORMAT_LINE: str = "ExecStart=/sbin/mkswap {device_path}"
UNIT_SWAP_ON_LINE: str = (
    "ExecStart=/sbin/swapon --priority {swap_priority} {device_path}"
)

# The names the task reads. The list lives next to the values it names and is
# read by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "COMPRESSOR",
    "SWAP_PRIORITY",
    "MEMORY_FRACTION_PERCENT",
    "FALLBACK_CPU_COUNT",
    "CPUINFO_PROCESSOR_KEY",
    "ALIGNMENT_BYTES",
    "RESET_BUSY_ATTEMPTS",
    "RESET_BUSY_RETRY_DELAY_SECONDS",
    "SERVICE_UNIT_NAME",
    "UNIT_TEMPLATE_FILE_NAME",
    "HOT_ADD_READABLE_MODE_BIT",
    "MODULE_NAME",
    "SWAP_SHOW_COMMAND",
    "MODULE_LOAD_COMMAND",
    "SWAP_OFF_COMMAND",
    "FORMAT_COMMAND",
    "SWAP_ON_COMMAND",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
    "UNIT_LOAD_LINE",
    "UNIT_ADD_READ_LINE",
    "UNIT_ADD_WRITE_LINE",
    "UNIT_ALGORITHM_LINE",
    "UNIT_DISKSIZE_LINE",
    "UNIT_FORMAT_LINE",
    "UNIT_SWAP_ON_LINE",
)
