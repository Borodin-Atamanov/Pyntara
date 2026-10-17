"""Task btrfs_points_setup: store the immutable save point and its work copy.

The section gives the machine the two things a recovery needs. The immutable
save point is a read-only snapshot of the running system; a session started from
it keeps its root filesystem in memory, so it works normally, leaves the point
untouched, and the same point serves the next recovery as well. The work copy is
a writable copy of the point: a session started from it is an ordinary system
that writes to the disk, so the user can simply keep working after a recovery.

The boot menu then shows all three sessions the machine carries. Both entries of
the snapshots are written by this section, because the generator of snapshot
entries would give one kernel parameter to every entry it writes, while the point
needs the parameter that keeps its root in memory and the copy needs the opposite;
the entry of the copy therefore carries no parameter and boots its subvolume
directly. The third entry starts the root the machine runs from with all its
changes in memory: the root is mounted read only and an overlay with a writable
layer in tmpfs is placed over it, so the session shows the machine as it was at
the boot and a reboot leaves nothing of it behind. The point is kept out of the
generated list, so it appears in the menu exactly once.

The session of the root in memory keeps its changes in memory, so anything that
survives a reboot has to be named: the swap file of the machine is usable there
only when it lies on a mount of its own, and every separate mount of the fstab
stays on the disk. The section reports a swap file on the root filesystem as a
warning, because the machine would run without swap, and writes the separate
mounts to the log, because a machine with a home of its own is an ordinary state
and not an incomplete configuration. The real boot directories are the one
exception the section acts on: a kernel update made in such a session would leave
the kernel on the disk and its modules in memory, so a unit remounts those
directories read only there.

The one-off recompression of the machine runs in the background while the other
tasks run, and this section waits for it to end before it takes the point, so
the point carries the finished state of the machine. The wait is bounded: a
machine whose recompression takes longer than the bound still receives its
point, taken from the state it has by then.

The snapshot of the running root needs no release of the swap of the machine:
the storage section keeps the swap area in a subvolume of its own, and a
subvolume is a barrier for a snapshot, so the point never carries the swap file
and the swap stays active while the point is taken. Measured on the target
machine on 2026-09-25: with the swap file inside the root subvolume the kernel
refuses the snapshot while that swap is active and refuses to activate the swap
file again once a snapshot shares its extents.

Every step reads the state of the machine before it writes and changes nothing
that is already in place: an existing point is never recreated, because it is
the state the user returns to, and an existing work copy is never overwritten,
because it carries the work of the user. A step that cannot be performed is
reported as a warning of a completed task
(docs/spec/btrfs-setup.md).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from pyntara import btrfs, fstab
from pyntara.config_edit import replace_line_by_string
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.package_set import install_missing_packages
from pyntara.utils import (
    run_command,
    service_is_active,
    substituted_command,
    task_data_dir,
)
from pyntara.values import btrfs_points_setup as values
from pyntara.values import btrfs_recompress as recompress_values
from pyntara.values import btrfs_setup as setup_values
from pyntara.values import engine as engine_values
from pyntara.values import swapfile_service_install as swapfile_values

# Warning of a machine whose root filesystem the section could not read.
ROOT_QUERY_WARNING: str = (
    "the root filesystem could not be read, so no save point was stored"
)

# Warning of a machine that does not run on btrfs.
NON_BTRFS_WARNING: str = (
    "the root filesystem is {filesystem_type} and not {btrfs}, so no save point "
    "was stored: the machine stays an ordinary system without a recovery point"
)

# Warning of a machine whose points subvolume is not mounted, which means the
# points section of the storage setup did not succeed.
POINTS_MOUNT_WARNING: str = (
    "{mount_point} is not a mounted filesystem, so the save point was not "
    "stored: the points subvolume has to be mounted first"
)

# Message of a session that was started from a copy of the root. The copy is
# either the save point, whose root runs as an overlay in memory, or the root of
# the machine started with its changes in memory. Both roots are copies of the
# root the machine was installed on, and the save point and the boot menu of the
# machine belong to the machine rather than to a session that reads a copy of it.
SNAPSHOT_SESSION_MESSAGE: str = (
    "this session was started from a copy of the root, so the save point and the "
    "boot menu of the machine are left alone"
)


def task(ctx: Context) -> TaskResult:
    """Store the immutable save point and its writable work copy."""

    mount = _root_filesystem()
    if mount is None:
        _log(ROOT_QUERY_WARNING)
        return TaskResult(success=True, changed=False, warnings=(ROOT_QUERY_WARNING,))
    if _runs_from_a_snapshot(mount):
        _log(SNAPSHOT_SESSION_MESSAGE)
        return TaskResult(
            success=True, changed=False, message=SNAPSHOT_SESSION_MESSAGE
        )
    if mount.filesystem_type != setup_values.BTRFS_FILESYSTEM_TYPE:
        warning = NON_BTRFS_WARNING.format(
            filesystem_type=mount.filesystem_type,
            btrfs=setup_values.BTRFS_FILESYSTEM_TYPE,
        )
        _log(warning, priority=engine_values.ERROR_PRIORITY)
        return TaskResult(success=True, changed=False, warnings=(warning,))
    if not _points_mounted():
        warning = POINTS_MOUNT_WARNING.format(
            mount_point=setup_values.POINTS_MOUNT_POINT
        )
        _log(warning, priority=engine_values.ERROR_PRIORITY)
        return TaskResult(success=True, changed=False, warnings=(warning,))

    warnings: list[str] = []
    changed = _install_tools(ctx, warnings)
    if not _point_is_stored():
        _wait_for_recompression(warnings)

    changed = _ensure_point(warnings) or changed
    point_present = _subvolume_exists(_point_directory())
    if point_present:
        changed = _ensure_work_copy(warnings) or changed
        changed = _write_boot_entry(ctx, warnings) or changed
        changed = _write_boot_read_only_units(ctx, warnings) or changed
        _report_in_memory_session_costs(warnings)
        changed = _write_menu_visibility(warnings) or changed
    else:
        warnings.append(
            f"the save point {_point_directory()} is not present, so no boot "
            f"entry was written and no work copy was made"
        )
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
    changed = _write_ignore_setting(warnings) or changed
    if changed or _is_forced(ctx):
        _refresh_menu(warnings)

    message = (
        f"the save point {values.POINT_NAME} and the work copy "
        f"{values.WORK_COPY_NAME} are stored in "
        f"{setup_values.POINTS_MOUNT_POINT}: the menu shows "
        f"{values.POINT_NAME} with the root filesystem in memory, "
        f"{values.WORK_COPY_NAME} as an ordinary writable system, and the "
        f"current root {values.GRUB_CURRENT_ROOT_ENTRY_NAME} with its changes "
        f"in memory"
    )
    _log(message)
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def _install_tools(ctx: Context, warnings: list[str]) -> bool:
    """Install the packages the boot loader tools of the section come from.

    The section asks the boot loader's own tools for the device and for the
    path of a kernel, and those tools come from grub2-common. The install goes
    through the shared package path, so the wait for the package lock, the
    index refresh and the retries are the same as in every other section.
    """

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
        _log("the boot loader tools of the section are installed")
    return bool(installed)


def _is_forced(ctx: Context) -> bool:
    """Answer whether the run asked this task to run its work again."""

    return ctx.task_name in ctx.force_tasks


def _root_filesystem() -> btrfs.MountedFilesystem | None:
    """Read the filesystem the root mount point stands on."""

    command = substituted_command(
        setup_values.FINDMNT_COMMAND, {"mount_point": str(setup_values.ROOT_MOUNT_POINT)}
    )
    return btrfs.read_mounted_filesystem(
        command, setup_values.STORAGE_COMMAND_TIMEOUT_SECONDS
    )


def _runs_from_a_snapshot(mount: btrfs.MountedFilesystem) -> bool:
    """Answer whether this session was started from one of the save points.

    A session of the immutable point runs its root as an overlay of that
    snapshot, and a session of the work copy runs it from a subvolume inside the
    points subvolume. Both roots are copies of the root the machine was installed
    on, and the save point and the boot menu of the machine belong to the machine
    rather than to a session that reads one of its copies. The subvolume of a
    mount is reported with a leading slash, as in device[/@points/Pyntara-work].
    """

    if mount.filesystem_type == setup_values.OVERLAY_FILESYSTEM_TYPE:
        return True
    points_path_prefix = (
        f"{setup_values.ROOT_MOUNT_POINT}{setup_values.POINTS_SUBVOLUME_NAME}"
        f"{setup_values.ROOT_MOUNT_POINT}"
    )
    return mount.subvolume.startswith(points_path_prefix)


def _point_is_stored() -> bool:
    """Answer whether the machine already carries the immutable save point.

    A machine that carries its point has nothing to take from the running
    system, so the section does not wait for the one-off recompression: the
    wait exists because the point should carry the finished state of the
    machine.
    """

    return _subvolume_exists(_point_directory())


def _points_mounted() -> bool:
    """Answer whether the points subvolume is mounted at its mount point."""

    return os.path.ismount(setup_values.POINTS_MOUNT_POINT)


def _point_directory() -> Path:
    """The directory of the immutable save point inside the points mount."""

    return setup_values.POINTS_MOUNT_POINT / values.POINT_NAME


def _work_copy_directory() -> Path:
    """The directory of the writable work copy inside the points mount."""

    return setup_values.POINTS_MOUNT_POINT / values.WORK_COPY_NAME


def _subvolume_exists(directory: Path) -> bool:
    """Answer whether a path carries a subvolume.

    The question goes to the tool, which answers for a subvolume and refuses a
    plain directory, so a machine that carries a directory where the point
    belongs is told apart from a machine that carries the point.
    """

    command = substituted_command(
        values.SUBVOLUME_SHOW_COMMAND, {"path": str(directory)}
    )
    return btrfs.subvolume_exists(command, values.STORAGE_COMMAND_TIMEOUT_SECONDS)


def _ensure_subvolume(
    source: Path, target: Path, *, read_only: bool, warnings: list[str]
) -> bool:
    """Store a subvolume at a path, unless the path is already taken.

    Three cases are told apart: the path already carries a subvolume, and then
    nothing is stored because the state the user keeps is never overwritten;
    the path carries something that is not a subvolume, and then the section
    reports it, because a snapshot into an existing directory is stored inside
    that directory under another name, where nobody looks for it; and a free
    path, which is where the snapshot is stored.
    """

    if _subvolume_exists(target):
        return False
    if target.exists():
        warnings.append(
            f"{target} carries no subvolume, so nothing was stored there: move "
            f"what occupies that path aside and run the section again"
        )
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    return _snapshot(source, target, read_only=read_only, warnings=warnings)


def _wait_for_recompression(warnings: list[str]) -> None:
    """Wait for the one-off recompression job to end, with a bound.

    Both sections write the same filesystem, and the point should carry the
    finished state of the machine, so the section waits. The wait is bounded by
    a declared limit: a machine whose job runs longer still receives its point,
    taken from the state the machine has by then.
    """

    unit = recompress_values.JOB_UNIT_NAME
    limit = recompress_values.JOB_WAIT_LIMIT_SECONDS
    if not service_is_active(unit, setup_values.STORAGE_COMMAND_TIMEOUT_SECONDS):
        return
    _log(f"waiting for the one-off recompression ({unit}) to end")
    started = time.monotonic()
    reported_minute = -1
    while service_is_active(unit, setup_values.STORAGE_COMMAND_TIMEOUT_SECONDS):
        elapsed = time.monotonic() - started
        if elapsed > limit:
            warnings.append(
                f"the one-off recompression ({unit}) still runs after "
                f"{limit // 60} minutes, so the save point was stored from the "
                f"state the machine has by now"
            )
            _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
            return
        minute = int(elapsed // 60)
        if minute != reported_minute:
            reported_minute = minute
            _log(
                f"the one-off recompression is still running "
                f"({minute} of {limit // 60} minutes)"
            )
        time.sleep(recompress_values.JOB_WAIT_POLL_SECONDS)
    _log("the one-off recompression finished")


def _ensure_point(warnings: list[str]) -> bool:
    """Store the immutable save point when it is not there yet."""

    directory = _point_directory()
    if _subvolume_exists(directory):
        _log(f"save point already stored: {directory}")
        _verify_point_is_read_only(directory, warnings)
        return False
    if not _ensure_subvolume(
        setup_values.ROOT_MOUNT_POINT,
        directory,
        read_only=True,
        warnings=warnings,
    ):
        return False
    _log(f"save point stored: {directory}")
    _verify_point_is_read_only(directory, warnings)
    return True


def _verify_point_is_read_only(directory: Path, warnings: list[str]) -> None:
    """Report a save point that does not answer that it is read only.

    A point that can be written is not a point: a session started from it would
    change the state the user returns to, and a machine that cannot answer the
    query leaves the user without the assurance that the point is safe. The
    section reports both cases and leaves the subvolume alone, because writing
    the property of an existing point is a change to the state the user keeps.
    """

    read_only = _read_only(directory)
    if read_only is True:
        return
    if read_only is False:
        warnings.append(
            f"the save point {directory} is not read only, so a session started "
            f"from it would change it"
        )
    else:
        warnings.append(
            f"the save point {directory} does not answer whether it is read "
            f"only, so check it with 'btrfs property get {directory} ro'"
        )
    _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)


def _ensure_work_copy(warnings: list[str]) -> bool:
    """Store the writable copy of the point when it is not there yet."""

    directory = _work_copy_directory()
    if _subvolume_exists(directory):
        _log(f"work copy already stored: {directory}")
        return False
    if not _ensure_subvolume(
        _point_directory(), directory, read_only=False, warnings=warnings
    ):
        return False
    _log(f"work copy stored: {directory}")
    return True


def _snapshot(
    source: Path, target: Path, *, read_only: bool, warnings: list[str]
) -> bool:
    """Store one subvolume as a snapshot of another one."""

    template = (
        values.READ_ONLY_SNAPSHOT_COMMAND if read_only else values.SNAPSHOT_COMMAND
    )
    command = substituted_command(
        template, {"source": str(source), "target": str(target)}
    )
    try:
        run_command(
            command, timeout=values.SNAPSHOT_TIMEOUT_SECONDS, check=True, capture=True
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warnings.append(
            f"the snapshot {target} could not be stored: {btrfs.failure_text(exc)}"
        )
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    return True


def _read_only(directory: Path) -> bool | None:
    """Read the read-only property of one subvolume."""

    command = substituted_command(
        values.READ_ONLY_PROPERTY_COMMAND, {"path": str(directory)}
    )
    return btrfs.read_only_property(command, values.STORAGE_COMMAND_TIMEOUT_SECONDS)


def _write_boot_entry(ctx: Context, warnings: list[str]) -> bool:
    """Write the boot entries of the three sessions into the grub.d directory.

    Each session gets a group of entries, one per kernel, so the menu offers
    every kernel for the read-only snapshot, for the writable copy and for the
    root the machine runs from with its changes in memory. A group that cannot
    be rendered leaves the file untouched: a file that carried only part of the
    entries would drop the other sessions from the menu.
    """

    mount = _root_filesystem()
    if mount is None:
        warning = ROOT_QUERY_WARNING
        warnings.append(warning)
        _log(warning, priority=engine_values.ERROR_PRIORITY)
        return False

    groups = (
        (
            values.GRUB_READ_ONLY_ENTRY_NAME,
            f"{setup_values.POINTS_SUBVOLUME_NAME}/{values.POINT_NAME}",
            values.OVERLAY_PARAMETER,
            values.READ_ONLY_BOOT_MESSAGE,
        ),
        (
            values.GRUB_WRITABLE_ENTRY_NAME,
            f"{setup_values.POINTS_SUBVOLUME_NAME}/{values.WORK_COPY_NAME}",
            "",
            values.WRITABLE_BOOT_MESSAGE,
        ),
        (
            values.GRUB_CURRENT_ROOT_ENTRY_NAME,
            _current_root_subvolume(mount),
            values.IN_MEMORY_PARAMETER,
            values.IN_MEMORY_BOOT_MESSAGE,
        ),
    )
    rendered: list[str] = []
    error: str | None = None
    for entry_name, subvolume, kernel_parameter, boot_message in groups:
        entries, group_error, notes = _render_entries(
            ctx,
            entry_name=entry_name,
            subvolume=subvolume,
            kernel_parameter=kernel_parameter,
            boot_message=boot_message,
        )
        warnings.extend(notes)
        for note in notes:
            _log(note, priority=engine_values.ERROR_PRIORITY)
        if group_error is not None:
            if error is None:
                error = group_error
            warnings.append(group_error)
            _log(group_error, priority=engine_values.ERROR_PRIORITY)
            continue
        rendered.append(entries)
    if error is not None:
        return False
    header = _template_text(ctx, values.GRUB_D_ENTRY_HEADER_FILE_NAME)
    content = header + "".join(rendered)
    path = values.GRUB_D_ENTRY_PATH
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            _log(f"boot entry already written: {path}")
            return False
        btrfs.write_file_atomically(path, content, values.GRUB_D_ENTRY_FILE_MODE)
    except OSError as exc:
        warnings.append(f"cannot write the boot entry {path}: {exc}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    _log(f"boot entry written: {path}")
    return True


def _current_root_subvolume(mount: btrfs.MountedFilesystem) -> str:
    """The subvolume the machine runs from, as the boot loader names it.

    findmnt reports a subvolume mount with a leading slash, as in /@, and the
    boot loader's rootflags option carries the path without it. A root on the top
    level of the filesystem carries no subvolume at all, which the option names
    as a slash.
    """

    return mount.subvolume.strip("/") or "/"


def _write_boot_read_only_units(ctx: Context, warnings: list[str]) -> bool:
    """Write and enable the unit that protects the real boot directories.

    The session of the root in memory keeps its changes in memory, but a boot
    directory of its own stays on the disk. A kernel update made there would
    leave the kernel on the disk and its modules in memory, and the ordinary
    system would no longer boot, so the unit remounts the directory read only in
    such a session. The conditions of the unit decide whether it applies at all:
    the kernel command line has to carry an overlayroot parameter, and the path
    has to be a mount point of its own; without the second condition a remount
    of a plain directory would turn the whole session read only.
    """

    mount_tool = shutil.which(values.MOUNT_TOOL_NAME)
    if mount_tool is None:
        warning = (
            f"the tool {values.MOUNT_TOOL_NAME} was not found, so a session of "
            f"the root in memory leaves the real boot directories writable"
        )
        warnings.append(warning)
        _log(warning, priority=engine_values.ERROR_PRIORITY)
        return False

    template = _template_text(ctx, values.BOOT_READ_ONLY_UNIT_TEMPLATE_FILE_NAME)
    content = btrfs.render_template(template, {"mount_tool": mount_tool})
    path = values.BOOT_READ_ONLY_UNIT_DIRECTORY / values.BOOT_READ_ONLY_UNIT_FILE_NAME
    changed = False
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            btrfs.write_file_atomically(
                path, content, values.BOOT_READ_ONLY_UNIT_FILE_MODE
            )
            changed = True
            _log(f"boot directory protection written: {path}")
    except OSError as exc:
        warnings.append(f"cannot write the boot directory protection {path}: {exc}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False

    if changed or _is_forced(ctx):
        _enable_boot_read_only_units(warnings)
    return changed


def _enable_boot_read_only_units(warnings: list[str]) -> None:
    """Enable the unit instances that protect the boot directories of the machine."""

    commands = (
        values.SYSTEMCTL_COMMAND + (values.SYSTEMCTL_DAEMON_RELOAD_ARGUMENT,),
        values.SYSTEMCTL_COMMAND
        + (values.SYSTEMCTL_ENABLE_ARGUMENT, *_boot_read_only_unit_names()),
    )
    for command in commands:
        try:
            run_command(
                list(command),
                timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
                check=True,
                capture=True,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(
                f"the call {' '.join(command)} failed: {btrfs.failure_text(exc)}"
            )
            _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)


def _boot_read_only_unit_names() -> tuple[str, ...]:
    """The unit instances that protect the boot directories of the machine.

    systemd names an instance after the path it carries, with the slashes turned
    into hyphens, so /boot becomes boot and /boot/efi becomes boot-efi.
    """

    prefix, _, suffix = values.BOOT_READ_ONLY_UNIT_FILE_NAME.partition("@")
    return tuple(
        f"{prefix}@{mount_point.strip('/').replace('/', '-')}{suffix}"
        for mount_point in values.BOOT_READ_ONLY_MOUNT_POINTS
    )


def _report_in_memory_session_costs(warnings: list[str]) -> None:
    """Report what the session of the root in memory cannot keep in memory.

    Two facts decide whether the session behaves as its name promises. A swap
    file is usable there only when it lies on a mount of its own, because the
    root is a read-only snapshot under a writable layer, so a file on the root
    filesystem is reported as a warning: the machine would run without swap. The
    separate mounts of the fstab keep writing to the disk, so changes there
    survive a reboot; that list is written to the log and not raised as a
    warning, because a machine whose home is a subvolume of its own is an
    ordinary state of an installation this project has not folded yet, and a
    warning would make every run of such a machine end with a nonzero exit code.
    The section changes nothing in either case: moving a swap file or a mount
    belongs to the sections that own them.
    """

    for swap_file in _active_swap_files():
        if _mount_point_of(swap_file) != str(setup_values.ROOT_MOUNT_POINT):
            continue
        warning = (
            f"the swap file {swap_file} lies on the root filesystem, so the "
            f"session of the root in memory has no swap: the file must lie on a "
            f"mount of its own"
        )
        warnings.append(warning)
        _log(warning, priority=engine_values.ERROR_PRIORITY)

    mounts = _mounts_that_stay_on_disk()
    if mounts:
        _log(
            f"in the session of the root in memory these mounts stay on the "
            f"disk, so changes there survive a reboot: {', '.join(mounts)}"
        )


def _active_swap_files() -> tuple[str, ...]:
    """The swap files the machine swaps on, as the swap tool lists them."""

    try:
        result = run_command(
            list(swapfile_values.SWAP_SHOW_COMMAND),
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=False,
            capture=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    files: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == swapfile_values.SWAP_FILE_TYPE:
            files.append(fields[0])
    return tuple(files)


def _mount_point_of(path: str) -> str | None:
    """The mount point a path belongs to, as the mount tool reports it."""

    command = substituted_command(values.MOUNT_POINT_COMMAND, {"path": path})
    try:
        result = run_command(
            command,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=False,
            capture=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.strip():
            return line.strip()
    return None


def _mounts_that_stay_on_disk() -> tuple[str, ...]:
    """The separate mounts that remain writable on the disk in such a session.

    The swap area is left out because it is meant to stay on the disk, the
    storage of the save points is left out because it is the recovery state of
    the machine rather than a place a session writes, and the boot directories
    are left out because the section remounts them read only there. The root
    itself is the session, so it is not listed either.
    """

    try:
        text = setup_values.FSTAB_PATH.read_text(encoding="utf-8")
    except OSError:
        return ()
    excluded = {
        str(setup_values.ROOT_MOUNT_POINT),
        str(setup_values.POINTS_MOUNT_POINT),
        str(swapfile_values.SWAPFILE_PATH.parent),
        *values.BOOT_READ_ONLY_MOUNT_POINTS,
    }
    mounts: list[str] = []
    for line in text.splitlines():
        fields = fstab.field_values(line)
        if fields is None:
            continue
        mount_point = fields[fstab.MOUNT_POINT_FIELD]
        if fields[fstab.FILESYSTEM_TYPE_FIELD] == values.FSTAB_SWAP_FILESYSTEM_TYPE:
            continue
        if mount_point in excluded or mount_point in mounts:
            continue
        mounts.append(mount_point)
    return tuple(mounts)


def _render_entries(
    ctx: Context,
    *,
    entry_name: str,
    subvolume: str,
    kernel_parameter: str,
    boot_message: str,
) -> tuple[str, str | None, tuple[str, ...]]:
    """Render one group of menu entries, one per kernel of the machine.

    The kernels are read from the boot directory of the machine and not from
    inside a snapshot: a machine whose boot directory is a partition or a
    subvolume of its own carries no kernel inside the snapshot, because a
    snapshot does not cross a mount. The device and the paths come from the boot
    loader's own tools, so this one code writes a correct entry whatever layout
    the machine has. The newest kernel carries the word of the newest kernel and
    every older kernel carries its version, so the menu names what the user
    chooses between. A kernel without its initial ramdisk is left out, because
    such an entry cannot boot, and the section reports it.
    """

    root_spec = _root_spec()
    if root_spec is None:
        return (
            "",
            (
                f"{setup_values.FSTAB_PATH} carries no line for "
                f"{setup_values.ROOT_MOUNT_POINT}, so no boot entry was written"
            ),
            (),
        )
    try:
        names = tuple(os.listdir(values.BOOT_MOUNT_POINT))
    except OSError as exc:
        return (
            "",
            f"cannot read the kernels of the machine {values.BOOT_MOUNT_POINT}: {exc}",
            (),
        )

    versions = sorted(
        (
            name[len(values.KERNEL_FILE_PREFIX) :]
            for name in names
            if name.startswith(values.KERNEL_FILE_PREFIX)
        ),
        key=_kernel_version_key,
        reverse=True,
    )
    kept = tuple(
        version
        for version in versions
        if (values.BOOT_MOUNT_POINT / f"{values.INITRD_FILE_PREFIX}{version}").is_file()
    )
    notes: list[str] = []
    left_out = tuple(version for version in versions if version not in kept)
    if left_out:
        notes.append(
            f"the machine {values.BOOT_MOUNT_POINT} carries kernels without "
            f"their initial ramdisk, so they have no entry: {', '.join(left_out)}"
        )
    if not kept:
        return (
            "",
            (
                f"the machine {values.BOOT_MOUNT_POINT} carries no kernel with "
                f"its initial ramdisk, so no boot entry was written"
            ),
            tuple(notes),
        )

    boot_uuid = _boot_device_uuid(notes)
    if boot_uuid is None:
        return (
            "",
            (
                f"the device of {values.BOOT_MOUNT_POINT} could not be read, so "
                f"no boot entry was written"
            ),
            tuple(notes),
        )
    search_line = values.GRUB_SEARCH_UUID_LINE.format(value=boot_uuid)

    body = _template_text(ctx, values.GRUB_D_ENTRY_BODY_FILE_NAME)
    rendered: list[str] = []
    for version in kept:
        kernel_path = _boot_loader_path(
            values.BOOT_MOUNT_POINT / f"{values.KERNEL_FILE_PREFIX}{version}", notes
        )
        initrd_path = _boot_loader_path(
            values.BOOT_MOUNT_POINT / f"{values.INITRD_FILE_PREFIX}{version}", notes
        )
        if kernel_path is None or initrd_path is None:
            continue
        newest = not rendered
        name = _menu_entry_name(entry_name, version, newest=newest)
        rendered.append(
            btrfs.render_template(
                body,
                {
                    "menu_title": name,
                    "entry_id": name,
                    "entry_class": values.GRUB_D_ENTRY_CLASS,
                    "search_line": search_line,
                    "kernel_path": kernel_path,
                    "root_spec": root_spec,
                    "subvolume": subvolume,
                    "overlay_parameter": kernel_parameter,
                    "boot_message": boot_message,
                    "initrd_path": initrd_path,
                },
            )
        )
    if not rendered:
        return (
            "",
            (
                f"the paths of the kernels of {values.BOOT_MOUNT_POINT} could not "
                f"be read, so no boot entry was written"
            ),
            tuple(notes),
        )
    return "".join(rendered), None, tuple(notes)


def _menu_entry_name(entry_name: str, version: str, *, newest: bool) -> str:
    """The name of one menu entry, used as its identifier and as its title.

    Every entry needs a name of its own, because the name is the documented way
    to preselect an entry, and two entries that share one make that choice depend
    on the order of the entries. The newest kernel carries the word of the newest
    kernel, so a selection written once keeps pointing at the newest kernel, and
    every older kernel carries its version. The boot loader accepts only letters,
    digits, underscores and hyphens in an identifier and refuses one that starts
    with a digit, so every other character of the version becomes a hyphen.
    """

    if newest:
        suffix = values.GRUB_LATEST_KERNEL_SUFFIX
    else:
        suffix = re.sub(r"[^A-Za-z0-9_-]", "-", version)
    return f"{entry_name}-{suffix}"


def _template_text(ctx: Context, file_name: str) -> str:
    """Read one template that ships with the section."""

    return task_data_dir(ctx.repo_root, ctx.task_name).joinpath(file_name).read_text(
        encoding="utf-8"
    )


def _boot_device_uuid(notes: list[str]) -> str | None:
    """The UUID of the filesystem that carries the kernels of the machine.

    The answer comes from grub-probe, the tool the boot menu itself uses, so the
    entry names the device the way this machine names it, whichever layout the
    machine has: a separate boot partition, a boot subvolume, or a boot
    directory inside the root subvolume.
    """

    command = substituted_command(
        values.GRUB_PROBE_UUID_COMMAND, {"path": str(values.BOOT_MOUNT_POINT)}
    )
    return _boot_loader_answer(command, notes, values.BOOT_MOUNT_POINT)


def _boot_loader_path(path: Path, notes: list[str]) -> str | None:
    """The path of a file as the boot loader names it on its own device."""

    command = substituted_command(values.GRUB_MKRELPATH_COMMAND, {"path": str(path)})
    return _boot_loader_answer(command, notes, path)


def _boot_loader_answer(
    command: list[str], notes: list[str], subject: Path
) -> str | None:
    """Run one boot loader tool and take its answer as it is.

    The answer is used unchanged: the tool names the device and the path the way
    the boot loader reads them, so the section must not post-process them. A
    tool that fails or answers nothing is reported with the text it gave, and
    the caller of the section logs every note.
    """

    try:
        result = run_command(
            command,
            timeout=values.STORAGE_COMMAND_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        notes.append(
            f"the boot loader tool could not read {subject}: "
            f"{btrfs.failure_text(exc)}"
        )
        return None
    answer = result.stdout.strip()
    if not answer:
        notes.append(f"the boot loader tool answered nothing for {subject}")
        return None
    return answer


def _kernel_version_key(version: str) -> tuple[int, ...]:
    """Order kernel versions by their numbers and not by their text."""

    return tuple(int(part) for part in re.findall(r"\d+", version))


def _root_spec() -> str | None:
    """Read the device field of the root line of the fstab."""

    try:
        text = setup_values.FSTAB_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    return fstab.spec_of_mount_point(text, str(setup_values.ROOT_MOUNT_POINT))


def _ignored_subvolumes() -> tuple[str, ...]:
    """The subvolumes the generator of snapshot entries must leave out.

    The section writes the boot entries of both snapshots itself, so the
    generator must not write entries of its own for them: the menu would show
    every snapshot twice, and the generated entries carry a kernel parameter and
    a name the section does not control.
    """

    prefix = setup_values.POINTS_SUBVOLUME_NAME
    return (f"{prefix}/{values.POINT_NAME}", f"{prefix}/{values.WORK_COPY_NAME}")


def _assignment_needle(directive: str) -> str:
    """The text that finds the line of one setting of a shell style file.

    The line editor of this project compares substrings, and the key of the wait
    of the boot menu begins with the key of its style: a needle of the bare key
    would replace the line of the style with the line of the wait, and the style
    would be lost. The needle therefore carries the assignment sign, which the
    key of the other setting does not contain.
    """

    key = directive.split(values.CONFIG_ASSIGNMENT_SIGN, 1)[0]
    return f"{key}{values.CONFIG_ASSIGNMENT_SIGN}"


def _write_menu_visibility(warnings: list[str]) -> bool:
    """Make the boot menu visible with a short wait, so a person can choose.

    This section writes the entries of both snapshots into the boot menu, and a
    person who cannot start the ordinary system reads them there: a menu the
    machine never shows is a menu nobody can choose from. The wait stays short,
    because the ordinary boot must not be held up, and the wait of a boot that
    failed is left to the distribution. Every other line, comment and value of
    the settings file survives.
    """

    wait_directive = values.GRUB_MENU_WAIT_DIRECTIVE_FORMAT.format(
        seconds=values.GRUB_MENU_WAIT_SECONDS
    )
    path = values.GRUB_DEFAULT_FILE_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read {path}: {exc}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False

    new_text = text
    for directive in (values.GRUB_MENU_STYLE_DIRECTIVE, wait_directive):
        new_text, _ = replace_line_by_string(
            new_text,
            _assignment_needle(directive),
            directive,
            fstab.COMMENT_SIGN,
        )
    if new_text == text:
        _log(
            f"the boot menu is already visible and waits "
            f"{values.GRUB_MENU_WAIT_SECONDS} seconds"
        )
        return False
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot write {path}: {exc}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    _log(
        f"the boot menu is visible and waits {values.GRUB_MENU_WAIT_SECONDS} "
        f"seconds: a person can choose {values.POINT_NAME} or "
        f"{values.WORK_COPY_NAME}"
    )
    return True


def _write_ignore_setting(warnings: list[str]) -> bool:
    """Keep both snapshots out of the generated list.

    The list is a setting of the generator of snapshot entries, and it may
    already carry subvolumes of an earlier run, so the setting is read, the
    missing subvolumes are added, and every entry that is already there stays.
    """

    path = setup_values.GRUB_BTRFS_CONFIG_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read {path}: {exc}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False

    key = values.GRUB_BTRFS_IGNORE_KEY
    present = _ignore_entries(text, key)
    missing = tuple(
        entry for entry in _ignored_subvolumes() if entry not in present
    )
    if not missing:
        _log("the generator already ignores both snapshots")
        return False

    directive = _ignore_directive((*present, *missing))
    new_text, changed = replace_line_by_string(
        text, key, directive, fstab.COMMENT_SIGN
    )
    if not changed:
        _log("the generator already ignores both snapshots")
        return False
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot write {path}: {exc}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    _log(f"the generator now ignores {', '.join(missing)}")
    return True


def _ignore_entries(text: str, key: str) -> tuple[str, ...]:
    """Read the subvolume entries the setting already carries.

    Only a line that is in force counts: a commented line of the same setting
    is an example of the generator, and the entries of a commented line are not
    kept.
    """

    sign = fstab.COMMENT_SIGN
    for line in text.splitlines():
        if line.lstrip().startswith(sign) or key not in line:
            continue
        return tuple(re.findall(r'"([^"]*)"', line))
    return ()


def _ignore_directive(entries: tuple[str, ...]) -> str:
    """The line of the setting that keeps the given subvolumes out of the list."""

    rendered = " ".join(
        values.GRUB_BTRFS_IGNORE_ENTRY_FORMAT.format(entry=entry)
        for entry in entries
    )
    return values.GRUB_BTRFS_IGNORE_DIRECTIVE_FORMAT.format(
        key=values.GRUB_BTRFS_IGNORE_KEY, entries=rendered
    )


def _refresh_menu(warnings: list[str]) -> bool:
    """Rebuild the boot menu so that it shows the entries just written.

    The daemon of the menu generator is off, so a rebuild cannot race a write of
    the generated list any more and the generated file stays in place.
    """

    try:
        run_command(
            values.UPDATE_GRUB_COMMAND,
            timeout=values.UPDATE_GRUB_TIMEOUT_SECONDS,
            check=True,
            capture=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"the boot menu could not be rebuilt: {btrfs.failure_text(exc)}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    _log("boot menu rebuilt")
    return True
