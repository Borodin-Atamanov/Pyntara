"""Values of the btrfs_recompress task.

The section compresses the data that is already on the machine once, runs the
aggressive balance that collects the freed space, and reports the result. The
work itself runs as a long job in the background while the rest of the run
continues, and a window on the desktop shows its journal, so the user sees what
the machine is doing without waiting in front of the installer
(docs/spec/btrfs-setup.md).

The machine facts this section shares with the setup section, such as the
filesystem type, the root mount point and the points mount point, are read from
the values module of that section, so one fact has one owner.
"""

from __future__ import annotations

from pathlib import Path

# Compression the existing data is rewritten with. The level is the highest
# level zstd accepts, because the rewrite happens once and the result pays for
# itself in every later write.
COMPRESSION_ALGORITHM: str = "zstd"
COMPRESSION_LEVEL: int = 15

# Mount points whose data is rewritten. The points mount point stays out on
# purpose: the points share their extents with the root, and a rewrite of that
# shared data would cost the machine the space the sharing saves.
DEFRAGMENTED_MOUNT_POINTS: tuple[str, ...] = ("/", "/home")

# Aggressive balance: every data chunk that is filled below this percentage is
# rewritten, which collects the space the compression freed.
BALANCE_USAGE_PERCENT: int = 73

# Free space the machine must have before the rewrite starts. A rewrite
# allocates new extents before it releases the old ones.
MINIMUM_FREE_GIB: int = 5

# Timeouts of the two long steps and of the whole job.
DEFRAGMENT_TIMEOUT_SECONDS: int = 7200
BALANCE_TIMEOUT_SECONDS: int = 3600

# The program of the section: the file of the clone under task_data, the path it
# is deployed to and the mode that makes it executable.
PROGRAM_FILE_NAME: str = "recompress_btrfs.py"
PROGRAM_DEPLOY_PATH: Path = Path("/usr/local/bin/pyntara-btrfs-recompress")
PROGRAM_FILE_MODE: int = 0o755

# The file the program writes when it finished. Its presence means the one-off
# work is done, so a rerun does not rewrite the whole filesystem again; the
# force mode of the run removes it and starts the work afresh.
DONE_MARKER_PATH: Path = Path("/var/lib/pyntara/btrfs-recompress-done")

# The transient systemd unit the job runs in. The unit is collected when it
# ends, so a finished job never blocks a later run.
JOB_UNIT_NAME: str = "pyntara-btrfs-recompress"
JOB_DESCRIPTION: str = "Compress the existing btrfs data and balance the chunks"
SYSTEMD_RUN_JOB_COMMAND: tuple[str, ...] = (
    "systemd-run",
    "--unit={unit}",
    "--collect",
    "--description={description}",
)
SYSTEMD_RUN_TIMEOUT_SECONDS: float = 60.0

# The window that shows the journal of the job. It runs in the service manager
# of the desktop user, so the window appears on the desktop of that user and
# the environment of the session does not have to be passed by hand.
TERMINAL_COMMAND: str = "konsole"
WINDOW_UNIT_NAME: str = "pyntara-btrfs-recompress-window"
SYSTEMD_RUN_WINDOW_COMMAND: tuple[str, ...] = (
    "systemd-run",
    "--machine",
    "{username}@.host",
    "--user",
    "--unit={unit}",
    "--collect",
    "{terminal}",
    "-e",
    "journalctl",
    "-f",
    "-u",
    "{job_unit}",
)

# How often the points section checks whether the job is still running while it
# waits for the one-off work to finish, and how long that wait may last. The
# state itself is read with the shared service query of utils.
JOB_WAIT_POLL_SECONDS: float = 10.0
JOB_WAIT_LIMIT_SECONDS: int = 10800

GIB_BYTES: int = 1073741824

# The value names this module declares.
READ_VALUE_NAMES: tuple[str, ...] = (
    "COMPRESSION_ALGORITHM",
    "COMPRESSION_LEVEL",
    "DEFRAGMENTED_MOUNT_POINTS",
    "BALANCE_USAGE_PERCENT",
    "MINIMUM_FREE_GIB",
    "DEFRAGMENT_TIMEOUT_SECONDS",
    "BALANCE_TIMEOUT_SECONDS",
    "PROGRAM_FILE_NAME",
    "PROGRAM_DEPLOY_PATH",
    "PROGRAM_FILE_MODE",
    "DONE_MARKER_PATH",
    "JOB_UNIT_NAME",
    "JOB_DESCRIPTION",
    "SYSTEMD_RUN_JOB_COMMAND",
    "SYSTEMD_RUN_TIMEOUT_SECONDS",
    "TERMINAL_COMMAND",
    "WINDOW_UNIT_NAME",
    "SYSTEMD_RUN_WINDOW_COMMAND",
    "JOB_WAIT_POLL_SECONDS",
    "JOB_WAIT_LIMIT_SECONDS",
    "GIB_BYTES",
)
