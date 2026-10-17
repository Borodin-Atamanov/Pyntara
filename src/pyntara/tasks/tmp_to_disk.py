"""Task tmp_to_disk: keep the temporary directory on the disk.

Kubuntu mounts /tmp from memory: the vendor unit tmp.mount, pulled in at boot by
local-fs.target, provides a tmpfs of half the installed memory. This task moves
the directory onto the disk of a desktop machine, where the memory is better
spent elsewhere.

The task reaches the target state in four independent steps, each of which is
reported on its own: it copies what the running memory mount holds to a staging
directory on the disk, removes the fstab entry that asks for the memory mount
through augeas, masks the unit systemd starts at boot, and stops that unit so
the running session sees the directory on the disk as well. The copy comes
first, because the contents of a memory mount disappear the moment the mount is
released; if the copy cannot be made, nothing else is changed and the contents
stay where they are. A staging copy that cannot be moved into place, because a
process still holds the memory mount, stays on the disk and the task names its
path as a warning.

A missing value costs the task and never the run: the names are reported in
plain words and the remaining tasks still run. The task is idempotent: when the
mount is already on the disk, the fstab carries no memory entry and the unit is
masked, it reports the target state and changes nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult, warned_result
from pyntara.package_set import failure_detail, install_missing_packages
from pyntara.utils import run_command, substituted_command, trim_whitespace
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import tmp_to_disk as values


def _augeas_fstab_base() -> str:
    """Augeas node of the fstab file this task edits."""

    return f"{engine_values.AUGEAS_FILES_NODE_PREFIX}{values.FSTAB_PATH}"


def _augeas_entry_node() -> str:
    """Augeas expression that selects the memory entries of the mount point.

    The selector is built from the declared mount point and filesystem type, so
    no path or type is written down twice, and it is resolved by augtool against
    the tree the Fstab lens produced.
    """

    selector = values.FSTAB_ENTRY_SELECTOR.format(
        mount_point=values.TMP_MOUNT_POINT,
        filesystem_type=values.TMPFS_TYPE,
    )
    return f"{_augeas_fstab_base()}/{selector}"


def _augeas_load_lines() -> tuple[str, ...]:
    """Lines of the augtool program that load the fstab alone.

    The load entry names one file and the lens that parses it, and the driver
    runs with --noautoload, so augtool never reads the configuration of the
    machine during a run.
    """

    return (
        engine_values.AUGEAS_LENS_LINE.format(lens=values.FSTAB_LENS),
        engine_values.AUGEAS_INCL_LINE.format(path=values.FSTAB_PATH),
        engine_values.AUGEAS_LOAD_LINE,
    )


def _run_augtool(
    script_lines: tuple[str, ...], timeout: float
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    """Run one augtool program and answer its result or the reason it failed."""

    script = "\n".join(script_lines) + "\n"
    try:
        result = run_command(
            list(engine_values.AUGTOOL_COMMAND),
            input=script,
            capture=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        detail = trim_whitespace(result.stderr) or trim_whitespace(result.stdout)
        return None, f"exit {result.returncode}: {detail}"
    return result, None


def _fstab_entry_present(timeout: float) -> tuple[bool | None, str | None]:
    """Whether the fstab asks for a memory mount of the temporary directory.

    The question is asked of augeas with the same selector the removal uses, so
    the answer and the edit can never disagree about which line is meant. A
    file no lens could parse, and a tool that could not run, are reported as the
    reason the answer is unknown instead of being read as "no entry".
    """

    node = _augeas_entry_node()
    result, reason = _run_augtool(
        (*_augeas_load_lines(), engine_values.AUGEAS_PRINT_LINE.format(node=node)),
        timeout,
    )
    if result is None:
        return None, f"cannot read {values.FSTAB_PATH}: {reason}"
    base = f"{_augeas_fstab_base()}/"
    present = any(line.startswith(base) for line in result.stdout.splitlines())
    return present, None


def _remove_fstab_entry(timeout: float) -> tuple[bool, str | None]:
    """Remove every memory entry of the mount point from the fstab."""

    node = _augeas_entry_node()
    result, reason = _run_augtool(
        (
            *_augeas_load_lines(),
            values.FSTAB_REMOVE_LINE.format(node=node),
            engine_values.AUGEAS_SAVE_LINE,
        ),
        timeout,
    )
    if result is None:
        return False, (
            f"cannot remove the {values.TMPFS_TYPE} entry of "
            f"{values.TMP_MOUNT_POINT} from {values.FSTAB_PATH}: {reason}"
        )
    return True, None


def _mount_type(path: Path, timeout: float) -> tuple[str | None, str | None]:
    """Filesystem type of the mount point, or None when it is not a mount.

    findmnt answers with no line and a nonzero status for a path that is not a
    mount point, which is the ordinary "not mounted" answer and not an error.
    Only a tool that could not run at all is reported as the reason why the
    answer is unknown.
    """

    command = substituted_command(values.MOUNT_TYPE_COMMAND, {"path": str(path)})
    try:
        result = run_command(command, capture=True, timeout=timeout, check=False)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return None, f"cannot read the mount of {path}: {exc}"
    if result.returncode != 0:
        return None, None
    return trim_whitespace(result.stdout) or None, None


def _unit_state(timeout: float) -> tuple[str | None, str | None]:
    """The enablement word systemctl answers for the unit, such as masked."""

    command = substituted_command(
        engine_values.SYSTEMCTL_IS_ENABLED_COMMAND,
        {"unit": values.SERVICE_UNIT_NAME},
    )
    try:
        result = run_command(command, capture=True, timeout=timeout, check=False)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return None, f"cannot read the state of {values.SERVICE_UNIT_NAME}: {exc}"
    return trim_whitespace(result.stdout) or None, None


def _mask_unit(timeout: float) -> tuple[bool, str | None]:
    """Mask the unit and reload the manager, so the next boot skips it."""

    command = substituted_command(
        values.SYSTEMCTL_MASK_COMMAND, {"unit": values.SERVICE_UNIT_NAME}
    )
    try:
        run_command(command, timeout=timeout)
        run_command(
            list(engine_values.SYSTEMCTL_DAEMON_RELOAD_COMMAND), timeout=timeout
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot mask {values.SERVICE_UNIT_NAME}: {exc}"
    return True, None


def _stop_unit(timeout: float) -> tuple[bool, str | None]:
    """Stop the mount unit, which releases the memory mount of this session."""

    command = substituted_command(
        engine_values.SYSTEMCTL_STOP_COMMAND, {"unit": values.SERVICE_UNIT_NAME}
    )
    try:
        run_command(command, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot stop {values.SERVICE_UNIT_NAME}: {exc}"
    return True, None


def _copy_tree(source: Path, destination: Path, timeout: float) -> tuple[bool, str | None]:
    """Copy the contents of one directory into another with the shared tool.

    Both paths are passed with a trailing slash, so rsync copies what the
    directories hold instead of the directories themselves.
    """

    command = [*values.COPY_COMMAND, f"{source}/", f"{destination}/"]
    try:
        run_command(command, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot copy {source} to {destination}: {exc}"
    return True, None


def _staging_directory() -> Path:
    """Path of the copy of this run, named with the configured moment format."""

    moment = datetime.now().astimezone().strftime(engine_values.DATETIME_FORMAT)
    return values.STAGING_DIRECTORY_PATH / f"{values.STAGING_DIRECTORY_PREFIX}{moment}"


def _directory_mode(path: Path) -> int | None:
    """Permission bits of a directory, or None when it cannot be read."""

    try:
        return path.stat().st_mode & values.DIRECTORY_PERMISSION_MASK
    except OSError:
        return None


def _present_word(present: bool | None) -> str:
    """The state of the fstab entry as one word of the progress line."""

    if present is None:
        return "unknown"
    return "present" if present else "absent"


def task(ctx: Context) -> TaskResult:
    """Move the temporary directory onto the disk; skip when already there.

    The goal is reached when the mount point is not a memory mount, the fstab
    carries no memory entry for it and the unit is masked. Otherwise the task
    copies the live contents to the disk, removes the fstab entry through
    augeas, masks the unit, stops it and moves the saved contents into place.
    Every step reports its own outcome, and a step that could not be performed
    becomes a warning of the completed task.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES)
    if absent:
        return TaskResult(
            success=True,
            message="the tmp_to_disk values are not declared, nothing was changed",
            warnings=(
                "the tmp_to_disk values are not declared: " + ", ".join(absent),
            ),
        )

    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    warnings: list[str] = []
    changed = False

    mount_type, mount_error = _mount_type(values.TMP_MOUNT_POINT, timeout)
    if mount_error is not None:
        warnings.append(mount_error)
    in_memory = mount_type == values.TMPFS_TYPE
    _log(
        f"checking mount of {values.TMP_MOUNT_POINT}: "
        f"{mount_type or 'not a mount point'}"
    )

    entry_present, fstab_error = _fstab_entry_present(timeout)
    if fstab_error is not None:
        warnings.append(fstab_error)
    _log(
        f"checking {values.FSTAB_PATH} for a {values.TMPFS_TYPE} entry of "
        f"{values.TMP_MOUNT_POINT}: {_present_word(entry_present)}"
    )

    unit_state, unit_error = _unit_state(timeout)
    if unit_error is not None:
        warnings.append(unit_error)
    masked = unit_state == values.UNIT_MASKED_STATE
    _log(
        f"checking unit {values.SERVICE_UNIT_NAME}: "
        f"{unit_state or 'state not read'}"
    )

    mode_ok = _directory_mode(values.TMP_MOUNT_POINT) == values.TMP_DIRECTORY_MODE
    _log(
        f"checking mode of {values.TMP_MOUNT_POINT}: "
        f"{'ready' if mode_ok else 'not the temporary directory mode'}"
    )

    if not warnings and not in_memory and entry_present is False and masked and mode_ok:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    missing, _, failures, package_warnings = install_missing_packages(
        ctx, values.PACKAGES
    )
    if missing:
        _log(f"installing tools: {', '.join(missing)}")
    warnings.extend(package_warnings)
    if failures:
        warnings.append(f"cannot install the tools: {failure_detail(failures)}")

    staging: Path | None = None
    if in_memory:
        staging = _staging_directory()
        _log(f"copying the contents of {values.TMP_MOUNT_POINT} to {staging}")
        try:
            staging.mkdir(parents=True)
            staging.chmod(values.STAGING_DIRECTORY_MODE)
        except OSError as exc:
            warnings.append(f"cannot create {staging}: {exc}")
            staging = None
        if staging is not None:
            copied, reason = _copy_tree(
                values.TMP_MOUNT_POINT, staging, timeout
            )
            if not copied:
                if reason is not None:
                    warnings.append(reason)
                return warned_result(
                    changed=changed,
                    message=(
                        f"nothing was changed: the contents of "
                        f"{values.TMP_MOUNT_POINT} could not be copied"
                    ),
                    warnings=warnings,
                )
            changed = True

    if entry_present:
        _log(
            f"removing the {values.TMPFS_TYPE} entry of "
            f"{values.TMP_MOUNT_POINT} from {values.FSTAB_PATH}"
        )
        removed, reason = _remove_fstab_entry(timeout)
        if removed:
            changed = True
        elif reason is not None:
            warnings.append(reason)

    if not masked:
        _log(f"masking unit {values.SERVICE_UNIT_NAME}")
        masked_now, reason = _mask_unit(timeout)
        if masked_now:
            changed = True
        elif reason is not None:
            warnings.append(reason)

    released = not in_memory
    if in_memory:
        _log(f"unmounting {values.TMP_MOUNT_POINT}")
        _, reason = _stop_unit(timeout)
        if reason is not None:
            warnings.append(reason)
        after_type, after_error = _mount_type(values.TMP_MOUNT_POINT, timeout)
        if after_error is not None:
            warnings.append(after_error)
        released = after_type != values.TMPFS_TYPE
        if released:
            changed = True
            _log(f"{values.TMP_MOUNT_POINT} is no longer a memory mount")
        else:
            warnings.append(
                f"{values.TMP_MOUNT_POINT} is still a memory mount; the next "
                "boot keeps it on the disk"
            )

    if staging is not None:
        if released:
            _log(f"moving the saved contents into {values.TMP_MOUNT_POINT}")
            moved, reason = _copy_tree(staging, values.TMP_MOUNT_POINT, timeout)
            if moved:
                changed = True
                try:
                    shutil.rmtree(staging)
                except OSError as exc:
                    warnings.append(f"cannot remove {staging}: {exc}")
            elif reason is not None:
                warnings.append(reason)
        else:
            warnings.append(
                f"the contents of the memory {values.TMP_MOUNT_POINT} are kept "
                f"in {staging}"
            )

    if released and _directory_mode(values.TMP_MOUNT_POINT) != values.TMP_DIRECTORY_MODE:
        try:
            values.TMP_MOUNT_POINT.chmod(values.TMP_DIRECTORY_MODE)
        except OSError as exc:
            warnings.append(
                f"cannot set the mode of {values.TMP_MOUNT_POINT}: {exc}"
            )
        else:
            changed = True

    if released:
        message = f"{values.TMP_MOUNT_POINT} is on the disk"
    else:
        message = f"{values.TMP_MOUNT_POINT} stays in memory until the next boot"
    return warned_result(changed=changed, message=message, warnings=warnings)
