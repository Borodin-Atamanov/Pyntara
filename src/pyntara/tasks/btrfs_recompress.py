"""Task btrfs_recompress: compress the existing data once and balance it.

The data that was written before the machine carried the compression option is
rewritten with the declared compression, and the data chunks are then balanced
so the freed space is collected. The work takes minutes on a real filesystem,
so it runs as a transient systemd unit in the background while the rest of the
provisioning continues, and a window on the desktop of the user follows the
journal of that unit, which is how the user sees the progress without waiting
in front of the installer.

The program of the section does the rewriting itself and writes a marker file
when every step succeeded. The marker is what makes the work one-off: a later
run finds it and skips the rewrite, and only the force mode of the run removes
it and starts the work again. A machine that is not btrfs, a machine without
room for the rewrite and a machine without a desktop session each cost their own
warning and nothing more (docs/spec/btrfs-setup.md).
"""

from __future__ import annotations

import shutil
import subprocess

from pyntara import btrfs
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command, substituted_command, task_data_dir
from pyntara.values import btrfs_recompress as values
from pyntara.values import btrfs_setup as setup_values
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values

# Warning of a machine that does not run on btrfs.
NON_BTRFS_WARNING: str = (
    "the root filesystem is {filesystem_type} and not {btrfs}, so the one-off "
    "recompression was skipped"
)

# Warning of a machine without room for the rewrite.
FREE_SPACE_WARNING: str = (
    "the machine has {free_gib} GiB free and the rewrite needs at least "
    "{needed_gib} GiB, so the recompression was not started"
)

# Warning of a machine without a desktop session, where the job runs unseen.
NO_WINDOW_WARNING: str = (
    "no desktop session was found, so the recompression runs in the background "
    "without a window: its messages are in the journal of {unit}"
)


def task(ctx: Context) -> TaskResult:
    """Rewrite the existing data once and balance the freed space."""

    mount = _root_filesystem()
    if mount is None:
        warning = "the root filesystem could not be read, so the recompression was skipped"
        _log(warning, priority=engine_values.ERROR_PRIORITY)
        return TaskResult(success=True, changed=False, warnings=(warning,))
    if mount.filesystem_type != setup_values.BTRFS_FILESYSTEM_TYPE:
        warning = NON_BTRFS_WARNING.format(
            filesystem_type=mount.filesystem_type,
            btrfs=setup_values.BTRFS_FILESYSTEM_TYPE,
        )
        _log(warning, priority=engine_values.ERROR_PRIORITY)
        return TaskResult(success=True, changed=False, warnings=(warning,))

    warnings: list[str] = []
    if values.DONE_MARKER_PATH.is_file() and not _is_forced(ctx):
        message = (
            f"the one-off recompression is already done: {values.DONE_MARKER_PATH} "
            f"exists, and only the force mode repeats it"
        )
        _log(message)
        return TaskResult(success=True, changed=False, message=message)

    free_bytes = shutil.disk_usage(setup_values.ROOT_MOUNT_POINT).free
    if free_bytes < values.MINIMUM_FREE_GIB * values.GIB_BYTES:
        warning = FREE_SPACE_WARNING.format(
            free_gib=free_bytes // values.GIB_BYTES,
            needed_gib=values.MINIMUM_FREE_GIB,
        )
        _log(warning, priority=engine_values.ERROR_PRIORITY)
        return TaskResult(success=True, changed=False, warnings=(warning,))

    changed = _deploy_program(ctx, warnings)
    if _is_forced(ctx):
        changed = _remove_done_marker(warnings) or changed
    started = _start_job(warnings)
    if started:
        changed = True
        _open_window(warnings)

    message = (
        f"the one-off recompression runs in the background as "
        f"{values.JOB_UNIT_NAME}: rewriting "
        f"{', '.join(values.DEFRAGMENTED_MOUNT_POINTS)} with "
        f"{values.COMPRESSION_ALGORITHM} level {values.COMPRESSION_LEVEL}, then "
        f"balancing the chunks filled below {values.BALANCE_USAGE_PERCENT} percent"
    )
    _log(message)
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def _is_forced(ctx: Context) -> bool:
    """Answer whether the run asked this task to run its work again."""

    return ctx.task_name in ctx.force_tasks


def _root_filesystem() -> btrfs.MountedFilesystem | None:
    """Read the filesystem the root mount point stands on."""

    command = substituted_command(
        setup_values.FINDMNT_COMMAND,
        {"mount_point": str(setup_values.ROOT_MOUNT_POINT)},
    )
    return btrfs.read_mounted_filesystem(
        command, setup_values.STORAGE_COMMAND_TIMEOUT_SECONDS
    )


def program_command() -> tuple[str, ...]:
    """The command line of the deployed program, built from the values.

    The option names are the interface of the program, and the end to end test
    of the section runs the program with this command line, so a name that
    drifts on one side fails the tests instead of failing on a target machine.
    """

    command: list[str] = [
        "--algorithm",
        values.COMPRESSION_ALGORITHM,
        "--level",
        str(values.COMPRESSION_LEVEL),
    ]
    for mount_point in values.DEFRAGMENTED_MOUNT_POINTS:
        command.extend(("--defragment-path", mount_point))
    command.extend(
        (
            "--balance-usage",
            str(values.BALANCE_USAGE_PERCENT),
            "--minimum-free-gib",
            str(values.MINIMUM_FREE_GIB),
            "--defragment-timeout-seconds",
            str(values.DEFRAGMENT_TIMEOUT_SECONDS),
            "--balance-timeout-seconds",
            str(values.BALANCE_TIMEOUT_SECONDS),
            "--marker-file",
            str(values.DONE_MARKER_PATH),
        )
    )
    return tuple(command)


def _remove_done_marker(warnings: list[str]) -> bool:
    """Remove the marker of the finished work, so the work runs again.

    The marker is what makes the work one-off, and a forced run starts the
    work again: leaving the mark of the earlier run in place would make the
    next run skip work that the forced run may not have finished.
    """

    try:
        values.DONE_MARKER_PATH.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        warnings.append(
            f"the marker {values.DONE_MARKER_PATH} could not be removed, so the "
            f"recompression may be skipped: {exc}"
        )
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    _log(f"marker {values.DONE_MARKER_PATH} removed for the forced run")
    return True


def _deploy_program(ctx: Context, warnings: list[str]) -> bool:
    """Put the program at its deployed path with the declared mode."""

    source = task_data_dir(ctx.repo_root, ctx.task_name) / values.PROGRAM_FILE_NAME
    try:
        content = source.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read the program {source}: {exc}")
        return False
    try:
        if (
            values.PROGRAM_DEPLOY_PATH.is_file()
            and values.PROGRAM_DEPLOY_PATH.read_text(encoding="utf-8") == content
        ):
            _log(f"program already deployed: {values.PROGRAM_DEPLOY_PATH}")
            return False
        btrfs.write_file_atomically(
            values.PROGRAM_DEPLOY_PATH, content, values.PROGRAM_FILE_MODE
        )
    except OSError as exc:
        warnings.append(f"cannot deploy the program to {values.PROGRAM_DEPLOY_PATH}: {exc}")
        return False
    _log(f"program deployed: {values.PROGRAM_DEPLOY_PATH}")
    return True


def _start_job(warnings: list[str]) -> bool:
    """Start the rewrite as a transient unit in the background."""

    command = [
        *substituted_command(
            values.SYSTEMD_RUN_JOB_COMMAND,
            {"unit": values.JOB_UNIT_NAME, "description": values.JOB_DESCRIPTION},
        ),
        str(values.PROGRAM_DEPLOY_PATH),
        *program_command(),
    ]
    try:
        run_command(
            command,
            timeout=values.SYSTEMD_RUN_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(
            f"the recompression job {values.JOB_UNIT_NAME} could not be "
            f"started: {exc}"
        )
        return False
    _log(f"job {values.JOB_UNIT_NAME} started")
    return True


def _open_window(warnings: list[str]) -> None:
    """Show the journal of the job in a window on the desktop of the user."""

    terminal = shutil.which(values.TERMINAL_COMMAND)
    if terminal is None:
        warnings.append(
            f"the terminal {values.TERMINAL_COMMAND} is not installed, so the "
            f"recompression runs without a window: its messages are in the "
            f"journal of {values.JOB_UNIT_NAME}"
        )
        return
    username = common_values.DESKTOP_USERNAME
    if not username:
        warnings.append(NO_WINDOW_WARNING.format(unit=values.JOB_UNIT_NAME))
        return
    command = substituted_command(
        values.SYSTEMD_RUN_WINDOW_COMMAND,
        {
            "username": username,
            "unit": values.WINDOW_UNIT_NAME,
            "terminal": terminal,
            "job_unit": values.JOB_UNIT_NAME,
        },
    )
    try:
        run_command(
            command,
            timeout=values.SYSTEMD_RUN_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(
            f"the window that shows the recompression could not be opened: "
            f"{exc}; the messages are in the journal of {values.JOB_UNIT_NAME}"
        )
        return
    _log(f"window {values.WINDOW_UNIT_NAME} shows the journal of {values.JOB_UNIT_NAME}")
