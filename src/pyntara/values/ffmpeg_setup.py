"""Values of the ffmpeg_setup task.

ffmpeg comes from the Ubuntu archive (the archive on Kubuntu 26.04 and newer
carries a complete GPL build), so apt is the whole install path. The task
then compiles the wayrecord capture engine from the C sources under
task_data/ffmpeg_setup/ to WAYRECORD_BIN_PATH and registers it as a trusted
application through the desktop entry rendered from
WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME, so KWin grants the screencast interface
without a portal dialog.
"""

from __future__ import annotations

from pathlib import Path

# The apt packages the task installs before it builds the engine: ffmpeg
# itself, the C toolchain, the Wayland and PipeWire development headers and
# pkg-config, which resolves the build flags.
PACKAGES: tuple[str, ...] = (
    "ffmpeg",
    "gcc",
    "libwayland-dev",
    "libpipewire-0.3-dev",
    "pkgconf",
)

# The system path the recording engine is built to.
WAYRECORD_BIN_PATH: Path = Path("/usr/local/bin/pyntara-wayrecord")

# The desktop entry that grants the screencast interface to the engine.
WAYRECORD_DESKTOP_PATH: Path = Path(
    "/usr/share/applications/pyntara-wayrecord.desktop"
)

# The mode of the built recording engine: it is executed by the user, so it
# needs the executable bits and nothing wider.
WAYRECORD_FILE_MODE: int = 0o755

# Names of the C sources of the engine under task_data/ffmpeg_setup/ of the
# clone, in compile order.
WAYRECORD_SOURCE_FILE_NAMES: tuple[str, ...] = (
    "wayrecord.c",
    "zkde-screencast-client.c",
)

# Name of the desktop entry template under task_data/ffmpeg_setup/ of the
# clone; the run substitutes the built engine path into it.
WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME: str = "pyntara-wayrecord.desktop"

# Suffix of the staged build next to the engine path: the compiler writes it
# there first, so an unchanged engine is detected by byte comparison.
WAYRECORD_BUILD_FILE_SUFFIX: str = ".build"

# Command that prints the compile flags of the engine; the run appends its
# output to the compile command.
WAYRECORD_BUILD_FLAGS_COMMAND: tuple[str, ...] = (
    "pkg-config",
    "--cflags",
    "--libs",
    "wayland-client",
    "libpipewire-0.3",
)

# Compiler command of the engine. The run replaces the output placeholder
# with the staged binary path and appends the sources and the flags.
WAYRECORD_COMPILE_COMMAND: tuple[str, ...] = ("gcc", "-O2", "-o", "{output}")

# Seconds the dpkg status query may take.
PACKAGE_STATUS_TIMEOUT_SECONDS: int = 30

# Retry attempts after a failed package install; the total number of
# attempts is this count plus one.
PACKAGE_INSTALL_RETRIES: int = 3

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not
# declare, instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "WAYRECORD_BIN_PATH",
    "WAYRECORD_DESKTOP_PATH",
    "WAYRECORD_FILE_MODE",
    "WAYRECORD_SOURCE_FILE_NAMES",
    "WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME",
    "WAYRECORD_BUILD_FILE_SUFFIX",
    "WAYRECORD_BUILD_FLAGS_COMMAND",
    "WAYRECORD_COMPILE_COMMAND",
    "PACKAGE_STATUS_TIMEOUT_SECONDS",
    "PACKAGE_INSTALL_RETRIES",
)
