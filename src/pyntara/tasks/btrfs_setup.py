"""Task btrfs_setup: ready the btrfs filesystem of the machine.

The section brings the filesystem to the state the rest of the run and the
later points expect: the tools are installed, the mounted system subvolumes
carry the compression the machine writes with, the top level subvolume that
holds the save points exists and is mounted, the maintenance schedule is
written, and the boot menu is ready to show the points. A machine whose root is
not btrfs receives a warning and nothing else, because such a machine stays a
usable machine.

Every step reads the state of the machine before it writes and does nothing
when the state is already the intended one, so a rerun is cheap and a machine
configured by an earlier run keeps its data. A step that cannot be performed is
reported as a warning of a completed task and never as a failure: the remaining
steps and the remaining tasks still run, and the entry point exits nonzero, so
an incomplete configuration is visible to scripts.

The section owns no save point and no work copy: the points section creates
them, and this section only prepares everything they need
(docs/spec/btrfs-setup.md).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pyntara import btrfs, fstab
from pyntara.config_edit import add_line_to_file, replace_line_by_string
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.package_set import install_missing_packages
from pyntara.utils import (
    download_command,
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
    task_data_dir,
)
from pyntara.values import btrfs_setup as values
from pyntara.values import engine as engine_values
from pyntara.values import swapfile_service_install as swapfile_values

# Warning of a machine whose root filesystem the section could not read.
ROOT_QUERY_WARNING: str = (
    "the root filesystem could not be read, so the btrfs setup was skipped"
)

# Warning of a machine that does not run on btrfs.
NON_BTRFS_WARNING: str = (
    "the root filesystem is {filesystem_type} and not {btrfs}, so the btrfs "
    "setup was skipped: the machine keeps working without compressed storage, "
    "without maintenance and without save points"
)


def task(ctx: Context) -> TaskResult:
    """Ready the btrfs filesystem of this machine."""

    mount = _root_filesystem()
    if mount is None:
        _log(ROOT_QUERY_WARNING)
        return TaskResult(success=True, changed=False, warnings=(ROOT_QUERY_WARNING,))
    if mount.filesystem_type != values.BTRFS_FILESYSTEM_TYPE:
        warning = NON_BTRFS_WARNING.format(
            filesystem_type=mount.filesystem_type,
            btrfs=values.BTRFS_FILESYSTEM_TYPE,
        )
        _log(warning, priority=engine_values.ERROR_PRIORITY)
        return TaskResult(success=True, changed=False, warnings=(warning,))

    _log(f"root filesystem: {mount.filesystem_type} on {mount.device}")
    warnings: list[str] = []
    changed = False

    changed = _install_tools(ctx, warnings) or changed
    changed = _turn_on_compression(warnings) or changed
    changed = _ensure_points_subvolume(mount, warnings) or changed
    changed = _ensure_swap_subvolume(mount, warnings) or changed
    changed = _write_maintenance_schedule(warnings) or changed
    changed = _prepare_menu(ctx, warnings) or changed

    message = (
        f"btrfs ready: the mounted subvolumes carry "
        f"{values.COMPRESSION_OPTION_ASSIGNMENT}, points mounted at "
        f"{values.POINTS_MOUNT_POINT}, the swap area in "
        f"{values.SWAP_SUBVOLUME_NAME} mounted at "
        f"{swapfile_values.SWAPFILE_PATH.parent}, maintenance written, menu "
        f"prepared"
    )
    _log(message)
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def _root_filesystem() -> btrfs.MountedFilesystem | None:
    """Read the filesystem the root mount point stands on."""

    command = substituted_command(
        values.FINDMNT_COMMAND, {"mount_point": str(values.ROOT_MOUNT_POINT)}
    )
    return btrfs.read_mounted_filesystem(
        command, values.STORAGE_COMMAND_TIMEOUT_SECONDS
    )


def _install_tools(ctx: Context, warnings: list[str]) -> bool:
    """Install the packages the section and its tools come from."""

    missing, installed, failures, package_warnings = install_missing_packages(
        ctx, values.PACKAGES
    )
    warnings.extend(package_warnings)
    warnings.extend(
        f"package {package} could not be installed: {reason}"
        for package, reason in failures
    )
    if installed:
        _log(f"installed: {', '.join(installed)}")
    if not missing:
        _log("every tool of the section is installed")
    return bool(installed)


def _turn_on_compression(warnings: list[str]) -> bool:
    """Put the compression option on the fstab lines and remount them."""

    try:
        text = values.FSTAB_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read {values.FSTAB_PATH}: {exc}")
        return False

    changed = False
    remounted: list[str] = []
    for mount_point in values.COMPRESSED_MOUNT_POINTS:
        text, line_changed = fstab.text_with_option(
            text, mount_point, values.COMPRESSION_OPTION_ASSIGNMENT
        )
        if line_changed:
            changed = True
            remounted.append(mount_point)
            _log(
                f"compression {values.COMPRESSION_OPTION_ASSIGNMENT} written "
                f"for {mount_point}"
            )
        else:
            _log(f"compression already set for {mount_point}")

    if changed:
        try:
            values.FSTAB_PATH.write_text(text, encoding="utf-8")
        except OSError as exc:
            warnings.append(f"cannot write {values.FSTAB_PATH}: {exc}")
            return False

    for mount_point in remounted or ():
        _remount(mount_point, warnings)
    return changed


def _remount(mount_point: str, warnings: list[str]) -> None:
    """Apply the fstab options of a mounted filesystem again."""

    command = substituted_command(
        values.REMOUNT_COMMAND, {"mount_point": mount_point}
    )
    try:
        run_command(
            command,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(
            f"{mount_point} could not be remounted, so the compression applies "
            f"from the next boot: {btrfs.failure_text(exc)}"
        )
        return
    _log(f"remounted {mount_point} with the declared options")


def _subvolume_paths() -> tuple[str, ...]:
    """Read the paths of the subvolumes of the filesystem of the root."""

    command = substituted_command(
        values.SUBVOLUME_LIST_COMMAND, {"path": str(values.ROOT_MOUNT_POINT)}
    )
    return btrfs.read_subvolume_paths(
        command, values.STORAGE_COMMAND_TIMEOUT_SECONDS
    )


def _ensure_points_subvolume(
    mount: btrfs.MountedFilesystem, warnings: list[str]
) -> bool:
    """Write the fstab line, create the points subvolume and mount it."""

    return _ensure_subvolume_mounted(
        mount,
        subvolume_name=values.POINTS_SUBVOLUME_NAME,
        mount_point=values.POINTS_MOUNT_POINT,
        warnings=warnings,
    )


def _ensure_swap_subvolume(
    mount: btrfs.MountedFilesystem, warnings: list[str]
) -> bool:
    """Move the swap area into a subvolume of its own and mount it there.

    A swap file inside the root subvolume either blocks the save point or is
    left dead by it: the kernel refuses to snapshot a subvolume that carries an
    active swap file of this filesystem, which btrfs reports as "Could not
    create subvolume: Text file busy", and it refuses to activate a swap file
    whose extents a snapshot shares, which btrfs reports as "swapon failed:
    Invalid argument" with "swapfile must not be copy-on-write" in the journal.
    Measured on the target machine on 2026-09-25: with the swap area in a
    subvolume of its own the snapshot of the root succeeds while the swap stays
    active, the swap file activates again after the snapshot, and the
    defragmentation of the root does not descend into that subvolume. The mount
    point is the directory of the swap file, read from the values of the swap
    section, so the path of the swap area is declared once.
    """

    swapfile_path = swapfile_values.SWAPFILE_PATH
    mount_point = swapfile_path.parent
    if _mount_point_is_mounted(mount_point):
        _log(f"{mount_point} is a mounted filesystem of its own, left as it is")
        return False
    swap_unit = swapfile_values.SERVICE_UNIT_NAME
    timeout = values.STORAGE_COMMAND_TIMEOUT_SECONDS
    swap_was_active = service_is_active(swap_unit, timeout)
    changed = False
    if swap_was_active:
        changed = _systemctl(values.SYSTEMCTL_STOP_COMMAND, swap_unit, warnings)
    try:
        if _remove_swap_file_of_the_root_subvolume(swapfile_path, warnings):
            changed = (
                _ensure_subvolume_mounted(
                    mount,
                    subvolume_name=values.SWAP_SUBVOLUME_NAME,
                    mount_point=mount_point,
                    warnings=warnings,
                )
                or changed
            )
    finally:
        if swap_was_active:
            _systemctl(values.SYSTEMCTL_START_COMMAND, swap_unit, warnings)
    return changed


def _remove_swap_file_of_the_root_subvolume(
    swapfile_path: Path, warnings: list[str]
) -> bool:
    """Remove the swap file that the mount of the swap subvolume would hide.

    A machine whose swap area was created before it had a subvolume of its own
    carries its swap file inside the root subvolume. The swap section creates
    the file again inside the new subvolume, because a file whose extents a
    snapshot shares cannot be activated any more, so the old file is removed
    here and the size named in the log is the room the machine wins back.
    Returns whether the way is clear for the mount.
    """

    if not swapfile_path.is_file():
        return True
    try:
        size_mib = swapfile_path.stat().st_size // (1024 * 1024)
        swapfile_path.unlink()
    except OSError as exc:
        warnings.append(
            f"the swap file {swapfile_path} lies inside the root subvolume and "
            f"could not be removed ({exc}), so the swap area stays inside that "
            f"subvolume: a save point of that root needs an inactive swap to "
            f"be taken"
        )
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    _log(f"{swapfile_path} of {size_mib} MiB removed from the root subvolume")
    return True


def _ensure_subvolume_mounted(
    mount: btrfs.MountedFilesystem,
    *,
    subvolume_name: str,
    mount_point: Path,
    warnings: list[str],
) -> bool:
    """Write the fstab line, create a subvolume and mount it at its mount point."""

    changed = _write_subvolume_fstab_line(subvolume_name, mount_point, warnings)
    if subvolume_name in _subvolume_paths():
        _log(f"subvolume {subvolume_name} already present")
    else:
        created = _create_subvolume(mount, subvolume_name, warnings)
        changed = created or changed
    if _mount_subvolume(mount_point, warnings):
        changed = True
    return changed


def _write_subvolume_fstab_line(
    subvolume_name: str, mount_point: Path, warnings: list[str]
) -> bool:
    """Append the line of a subvolume mount to the fstab when it is missing."""

    try:
        text = values.FSTAB_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read {values.FSTAB_PATH}: {exc}")
        return False

    mount_point_text = str(mount_point)
    if fstab.carries_mount_point(text, mount_point_text):
        _log(f"fstab already carries the line for {mount_point_text}")
        return False

    spec = fstab.spec_of_mount_point(text, str(values.ROOT_MOUNT_POINT))
    if spec is None:
        warnings.append(
            f"{values.FSTAB_PATH} carries no line for {values.ROOT_MOUNT_POINT}, "
            f"so the line for {mount_point_text} was not written"
        )
        return False

    line = values.SUBVOLUME_FSTAB_LINE_FORMAT.format(
        spec=spec,
        mount_point=mount_point_text,
        filesystem_type=values.BTRFS_FILESYSTEM_TYPE,
        subvolume=subvolume_name,
    )
    try:
        written = add_line_to_file(
            values.FSTAB_PATH, line, fstab.COMMENT_SIGN
        )
    except OSError as exc:
        warnings.append(f"cannot write {values.FSTAB_PATH}: {exc}")
        return False
    if written:
        _log(f"fstab line for {mount_point_text} written")
    return written


def _create_subvolume(
    mount: btrfs.MountedFilesystem, subvolume_name: str, warnings: list[str]
) -> bool:
    """Create a subvolume through a temporary mount of the top level."""

    try:
        values.TOPLEVEL_MOUNT_POINT.mkdir(parents=True, exist_ok=True)
        values.TOPLEVEL_MOUNT_POINT.chmod(values.TOPLEVEL_DIRECTORY_MODE)
    except OSError as exc:
        warnings.append(f"cannot create {values.TOPLEVEL_MOUNT_POINT}: {exc}")
        return False

    mount_command = substituted_command(
        values.TOPLEVEL_MOUNT_COMMAND,
        {
            "subvolume_id": values.TOPLEVEL_SUBVOLUME_ID,
            "device": mount.device,
            "mount_point": str(values.TOPLEVEL_MOUNT_POINT),
        },
    )
    try:
        run_command(
            mount_command,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(
            f"the top level of {mount.device} could not be mounted, so the "
            f"subvolume {subvolume_name} was not created: {btrfs.failure_text(exc)}"
        )
        return False

    created = False
    try:
        create_command = substituted_command(
            values.SUBVOLUME_CREATE_COMMAND,
            {"path": str(values.TOPLEVEL_MOUNT_POINT / subvolume_name)},
        )
        run_command(
            create_command,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
        created = True
        _log(f"subvolume {subvolume_name} created")
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(
            f"the subvolume {subvolume_name} could not be "
            f"created: {btrfs.failure_text(exc)}"
        )
    finally:
        _unmount_toplevel(warnings)
    return created


def _unmount_toplevel(warnings: list[str]) -> None:
    """Release the temporary mount of the top level."""

    command = substituted_command(
        values.TOPLEVEL_UNMOUNT_COMMAND,
        {"mount_point": str(values.TOPLEVEL_MOUNT_POINT)},
    )
    try:
        run_command(
            command,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(
            f"{values.TOPLEVEL_MOUNT_POINT} could not be unmounted: {btrfs.failure_text(exc)}"
        )


def _mount_point_is_mounted(mount_point: Path) -> bool:
    """Answer whether a mount point carries a mounted filesystem."""

    return os.path.ismount(mount_point)


def _mount_subvolume(mount_point: Path, warnings: list[str]) -> bool:
    """Mount a subvolume at its mount point when it is not mounted there."""

    if _mount_point_is_mounted(mount_point):
        _log(f"{mount_point} is mounted")
        return False
    try:
        mount_point.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(f"cannot create {mount_point}: {exc}")
        return False
    command = substituted_command(
        values.MOUNT_COMMAND, {"mount_point": str(mount_point)}
    )
    try:
        run_command(
            command,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(
            f"{mount_point} could not be mounted: {btrfs.failure_text(exc)}"
        )
        return False
    _log(f"{mount_point} mounted")
    return True


def _systemctl(
    command: tuple[str, ...], unit: str, warnings: list[str]
) -> bool:
    """Run one systemctl call of the section on one unit."""

    try:
        run_command(
            substituted_command(command, {"unit": unit}),
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"the call on {unit} failed: {btrfs.failure_text(exc)}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    return True


def _write_maintenance_schedule(warnings: list[str]) -> bool:
    """Write the maintenance lines and set the state of its timers."""

    changed = _write_maintenance_directives(warnings)
    for timer in values.MAINTENANCE_ENABLED_TIMERS:
        changed = _set_timer_state(timer, enabled=True, warnings=warnings) or changed
    for timer in values.MAINTENANCE_DISABLED_TIMERS:
        changed = _set_timer_state(timer, enabled=False, warnings=warnings) or changed
    return changed


def _write_maintenance_directives(warnings: list[str]) -> bool:
    """Write every declared maintenance line into its configuration file."""

    try:
        text = values.MAINTENANCE_CONFIG_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read {values.MAINTENANCE_CONFIG_PATH}: {exc}")
        return False

    changed = False
    for directive in values.MAINTENANCE_DIRECTIVES:
        key = directive.split("=", 1)[0]
        text, line_changed = replace_line_by_string(text, key, directive)
        changed = changed or line_changed
    if not changed:
        _log("maintenance schedule already written")
        return False
    try:
        values.MAINTENANCE_CONFIG_PATH.write_text(text, encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot write {values.MAINTENANCE_CONFIG_PATH}: {exc}")
        return False
    _log("maintenance schedule written")
    return True


def _set_timer_state(name: str, *, enabled: bool, warnings: list[str]) -> bool:
    """Turn one maintenance timer on or off when it is not in that state."""

    state_word = "enabled" if enabled else "disabled"
    if service_is_enabled(name, values.STORAGE_COMMAND_TIMEOUT_SECONDS) == enabled:
        _log(f"{name} is already {state_word}")
        return False
    template = (
        values.SYSTEMCTL_ENABLE_COMMAND
        if enabled
        else values.SYSTEMCTL_DISABLE_COMMAND
    )
    command = substituted_command(template, {"unit": name})
    try:
        run_command(
            command,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(f"{name} could not be {state_word}: {btrfs.failure_text(exc)}")
        return False
    _log(f"{name} {state_word}")
    return True


def _prepare_menu(ctx: Context, warnings: list[str]) -> bool:
    """Install the menu generator, point it at the points and start its daemon."""

    changed = _build_menu_generator(warnings)
    changed = _write_kernel_parameters(warnings) or changed
    dropin_changed = _deploy_daemon_dropin(ctx, warnings)
    daemon_changed = _reload_and_start_daemon(dropin_changed, warnings)
    return changed or dropin_changed or daemon_changed


def _build_menu_generator(warnings: list[str]) -> bool:
    """Install the menu generator from its pinned sources when it is missing."""

    if values.GRUB_BTRFS_INSTALLED_PATH.is_file():
        _log(f"menu generator already installed: {values.GRUB_BTRFS_INSTALLED_PATH}")
        return False

    try:
        values.GRUB_BTRFS_BUILD_DIRECTORY.mkdir(parents=True, exist_ok=True)
        values.GRUB_BTRFS_BUILD_DIRECTORY.chmod(
            values.GRUB_BTRFS_BUILD_DIRECTORY_MODE
        )
    except OSError as exc:
        warnings.append(f"cannot create {values.GRUB_BTRFS_BUILD_DIRECTORY}: {exc}")
        return False

    archive = values.GRUB_BTRFS_BUILD_DIRECTORY / (
        values.GRUB_BTRFS_ARCHIVE_FILE_NAME.format(commit=values.GRUB_BTRFS_COMMIT)
    )
    url = values.GRUB_BTRFS_SOURCE_URL.format(commit=values.GRUB_BTRFS_COMMIT)
    source_directory = values.GRUB_BTRFS_BUILD_DIRECTORY / (
        values.GRUB_BTRFS_SOURCE_DIRECTORY_NAME.format(
            commit=values.GRUB_BTRFS_COMMIT
        )
    )
    _log(f"downloading the menu generator from {url}")
    try:
        run_command(
            download_command(archive, url),
            timeout=engine_values.CURL_DOWNLOAD_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
        run_command(
            substituted_command(
                values.GRUB_BTRFS_EXTRACT_COMMAND,
                {
                    "archive": str(archive),
                    "directory": str(values.GRUB_BTRFS_BUILD_DIRECTORY),
                },
            ),
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
        run_command(
            substituted_command(
                values.GRUB_BTRFS_BUILD_COMMAND,
                {"source_directory": str(source_directory)},
            ),
            timeout=values.GRUB_BTRFS_BUILD_TIMEOUT_SECONDS,
            check=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(f"the menu generator could not be built: {btrfs.failure_text(exc)}")
        return False

    if not values.GRUB_BTRFS_INSTALLED_PATH.is_file():
        warnings.append(
            f"the build finished without {values.GRUB_BTRFS_INSTALLED_PATH}, so "
            f"the boot menu shows no save points"
        )
        return False
    _log("menu generator installed")
    return True


def _write_kernel_parameters(warnings: list[str]) -> bool:
    """Write the empty kernel parameter line of the generator.

    The generator adds the parameter line to every entry it writes. The line is
    empty on this machine, so an entry boots its subvolume directly and writes
    to the disk; the immutable point, which needs the root filesystem in
    memory, carries that parameter in its own entry instead.
    """

    changed, failure = btrfs.write_config_directive(
        values.GRUB_BTRFS_CONFIG_PATH,
        values.GRUB_BTRFS_KERNEL_PARAMETERS_KEY,
        values.GRUB_BTRFS_KERNEL_PARAMETERS_DIRECTIVE,
    )
    if failure is not None:
        warnings.append(failure)
        return False
    if changed:
        _log(f"{values.GRUB_BTRFS_KERNEL_PARAMETERS_KEY} written")
    else:
        _log(f"{values.GRUB_BTRFS_KERNEL_PARAMETERS_KEY} is already empty")
    return changed


def _deploy_daemon_dropin(ctx: Context, warnings: list[str]) -> bool:
    """Write the drop-in that points the daemon at the points mount."""

    template_path = (
        task_data_dir(ctx.repo_root, ctx.task_name)
        / values.GRUB_BTRFS_DAEMON_DROPIN_FILE_NAME
    )
    try:
        body = btrfs.render_template(
            template_path.read_text(encoding="utf-8"),
            {
                "points_mount_point": str(values.POINTS_MOUNT_POINT),
                "daemon_path": str(values.GRUB_BTRFS_DAEMON_PATH),
                "syslog_option": values.GRUB_BTRFS_DAEMON_SYSLOG_OPTION,
                "watch_option": values.GRUB_BTRFS_DAEMON_WATCH_OPTION,
            },
        )
    except OSError as exc:
        warnings.append(f"cannot read the drop-in template {template_path}: {exc}")
        return False

    target = values.GRUB_BTRFS_DAEMON_DROPIN_PATH
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == body:
            _log("daemon drop-in already current")
            return False
        btrfs.write_file_atomically(
            target, body, values.GRUB_BTRFS_DAEMON_DROPIN_FILE_MODE
        )
    except OSError as exc:
        warnings.append(f"cannot write {target}: {exc}")
        return False
    _log(f"daemon drop-in written: {target}")
    return True


def _reload_and_start_daemon(dropin_changed: bool, warnings: list[str]) -> bool:
    """Reload systemd, enable the menu daemon and restart it when it changed."""

    unit = values.GRUB_BTRFS_DAEMON_UNIT_NAME
    try:
        run_command(
            values.SYSTEMCTL_DAEMON_RELOAD_COMMAND,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        warnings.append(f"systemd could not reload its units: {btrfs.failure_text(exc)}")
        return False

    already_enabled = service_is_enabled(
        unit, values.STORAGE_COMMAND_TIMEOUT_SECONDS
    )
    changed = False
    for template, needed in (
        (values.SYSTEMCTL_ENABLE_COMMAND, not already_enabled),
        (values.SYSTEMCTL_RESTART_COMMAND, dropin_changed or not already_enabled),
    ):
        if not needed:
            continue
        command = substituted_command(template, {"unit": unit})
        try:
            run_command(
                command,
                timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
                check=True,
                capture=True,
            )
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            warnings.append(f"{unit} could not be started: {btrfs.failure_text(exc)}")
            return changed
        changed = True
    _log(f"{unit} watches {values.POINTS_MOUNT_POINT}")
    return changed
