"""Values of the zswap_service task.

The task writes these parameters into the kernel attribute directory below and
installs a systemd oneshot service that repeats the same writes at every boot.
The values are aggressive: zstd compresses stronger than the lzo default, the
pool may grow to the configured percentage of RAM, and the accept threshold
keeps a hysteresis instead of the 100 that disables it. Kernel 7.0 of Kubuntu
26.04 exposes exactly these five parameters; zpool and same_filled_pages_enabled
no longer exist, because zsmalloc is the only pool and same-filled page handling
is always on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ZswapParameter:
    """One kernel attribute of the zswap module and the text it is given.

    The attribute name is the name the kernel itself uses, so a parameter
    the kernel drops is removed by deleting its record and nothing else.
    The text is what the kernel receives: a boolean attribute carries the Y
    or N spelling sysfs reports, a number carries the digits the attribute
    expects.
    """

    attribute_name: str
    value: str


# The zswap parameters, in the order the task writes them: enable the cache
# first, then the compressor, the pool ceiling, the re-accept threshold and the
# shrinker.
PARAMETER_VALUES: tuple[ZswapParameter, ...] = (
    # Compressed cache switched on.
    ZswapParameter("enabled", "Y"),
    # Strongest compressor available on Kubuntu.
    ZswapParameter("compressor", "zstd"),
    # Maximum share of RAM the compressed pool may occupy, in percent.
    ZswapParameter("max_pool_percent", "12"),
    # Percent of the pool limit at which zswap starts accepting pages again
    # after being full; below 100 the pool keeps a hysteresis.
    ZswapParameter("accept_threshold_percent", "87"),
    # The shrinker proactively writes cold pages to the backing swap.
    ZswapParameter("shrinker_enabled", "Y"),
)

# Kernel attribute directory of the zswap module: the only path of the task.
PARAMETERS_DIR_PATH: Path = Path("/sys/module/zswap/parameters")

# Name of the oneshot unit template under task_data/zswap_service/ of the clone,
# rendered with the expanded ExecStart block.
UNIT_TEMPLATE_FILE_NAME: str = "zswap.service"

# Line of the unit that repeats one parameter write at boot, with the value and
# the attribute path as its placeholders.
UNIT_EXEC_LINE_TEMPLATE: str = "ExecStart=/bin/sh -c 'echo {value} > {path}'"

# Name of the systemd oneshot service unit that repeats the writes at boot.
SERVICE_UNIT_NAME: str = "zswap.service"

# systemctl calls of the task, each carrying the unit name as its placeholder.
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = (
    "systemctl",
    "daemon-reload",
)
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PARAMETER_VALUES",
    "PARAMETERS_DIR_PATH",
    "UNIT_TEMPLATE_FILE_NAME",
    "UNIT_EXEC_LINE_TEMPLATE",
    "SERVICE_UNIT_NAME",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
)
