"""Task btrfs_points_setup: store the immutable save point and its work copy.

The section gives the machine the two things a recovery needs. The immutable
save point is a read-only snapshot of the running system; a session started from
it keeps its root filesystem in memory, so it works normally, leaves the point
untouched, and the same point serves the next recovery as well. The work copy is
a writable copy of the point: a session started from it is an ordinary system
that writes to the disk, so the user can simply keep working after a recovery.

The boot menu then shows both. The entry of the point is written by this section,
because it carries a kernel parameter that the generator of snapshot entries
would give to every entry it writes; the entry of the copy is written by that
generator, with the parameter left empty, which is what makes the copy boot
directly from its subvolume. The point is kept out of the generated list, so it
appears in the menu exactly once.

The one-off recompression of the machine runs in the background while the other
tasks run, and this section waits for it to end before it takes the point, so
the point carries the finished state of the machine. The wait is bounded: a
machine whose recompression takes longer than the bound still receives its
point, taken from the state it has by then.

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
import subprocess
import time
from pathlib import Path

from pyntara import btrfs, fstab
from pyntara.config_edit import replace_line_by_string
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
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


def task(ctx: Context) -> TaskResult:
    """Store the immutable save point and its writable work copy."""

    mount = _root_filesystem()
    if mount is None:
        _log(ROOT_QUERY_WARNING)
        return TaskResult(success=True, changed=False, warnings=(ROOT_QUERY_WARNING,))
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
    if not _point_is_stored():
        _wait_for_recompression(warnings)

    changed = _ensure_point(warnings)
    point_present = _subvolume_exists(_point_directory())
    if point_present:
        changed = _ensure_work_copy(warnings) or changed
        changed = _write_boot_entry(ctx, warnings) or changed
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
        f"{values.POINT_NAME} with the root filesystem in memory and "
        f"{values.WORK_COPY_NAME} as an ordinary writable system"
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
        setup_values.FINDMNT_COMMAND, {"mount_point": str(setup_values.ROOT_MOUNT_POINT)}
    )
    return btrfs.read_mounted_filesystem(
        command, setup_values.STORAGE_COMMAND_TIMEOUT_SECONDS
    )


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
            _log(f"the one-off recompression is still running ({minute} minutes)")
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
    """Write the boot entry of the immutable point into the grub.d directory."""

    entries, error, notes = _render_entries(ctx, _point_directory())
    warnings.extend(notes)
    for note in notes:
        _log(note, priority=engine_values.ERROR_PRIORITY)
    if error is not None:
        warnings.append(error)
        _log(error, priority=engine_values.ERROR_PRIORITY)
        return False
    header = _template_text(ctx, values.GRUB_D_ENTRY_HEADER_FILE_NAME)
    content = header + entries
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


def _render_entries(
    ctx: Context, point_directory: Path
) -> tuple[str, str | None, tuple[str, ...]]:
    """Render one menu entry per kernel of the point.

    The newest kernel carries the plain title of the point and every older
    kernel carries the kernel version in its title, so the menu names what the
    user chooses between. A kernel without its initial ramdisk is left out,
    because such an entry cannot boot, and the section reports it.
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
    boot_directory = point_directory / values.BOOT_DIRECTORY_NAME
    try:
        names = tuple(os.listdir(boot_directory))
    except OSError as exc:
        return (
            "",
            f"cannot read the kernels of the point {boot_directory}: {exc}",
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
        if (boot_directory / f"{values.INITRD_FILE_PREFIX}{version}").is_file()
    )
    notes: tuple[str, ...] = ()
    left_out = tuple(version for version in versions if version not in kept)
    if left_out:
        notes = (
            (
                f"the point {point_directory} carries kernels without their "
                f"initial ramdisk, so they have no entry: {', '.join(left_out)}"
            ),
        )
    if not kept:
        return (
            "",
            (
                f"the point {point_directory} carries no kernel with its "
                f"initial ramdisk, so no boot entry was written"
            ),
            notes,
        )

    body = _template_text(ctx, values.GRUB_D_ENTRY_BODY_FILE_NAME)
    subvolume = f"{setup_values.POINTS_SUBVOLUME_NAME}/{values.POINT_NAME}"
    rendered: list[str] = []
    for index, version in enumerate(kept):
        title = (
            values.POINT_NAME
            if index == 0
            else f"{values.POINT_NAME} ({version})"
        )
        rendered.append(
            btrfs.render_template(
                body,
                {
                    "menu_title": title,
                    "entry_id": values.GRUB_D_ENTRY_ID,
                    "entry_class": values.GRUB_D_ENTRY_CLASS,
                    "search_line": _search_line(root_spec),
                    "kernel_path": _grub_path(
                        point_directory, f"{values.KERNEL_FILE_PREFIX}{version}"
                    ),
                    "root_spec": root_spec,
                    "subvolume": subvolume,
                    "overlay_parameter": values.OVERLAY_PARAMETER,
                    "initrd_path": _grub_path(
                        point_directory, f"{values.INITRD_FILE_PREFIX}{version}"
                    ),
                },
            )
        )
    return "".join(rendered), None, notes


def _template_text(ctx: Context, file_name: str) -> str:
    """Read one template that ships with the section."""

    return task_data_dir(ctx.repo_root, ctx.task_name).joinpath(file_name).read_text(
        encoding="utf-8"
    )
def _grub_path(directory: Path, file_name: str) -> str:
    """The path of a file inside the point as the boot loader names it.

    The boot loader reaches the point through the top level of the filesystem,
    so the path starts at the points subvolume and not at the mount point of
    that subvolume in the running system.
    """

    return (
        f"/{setup_values.POINTS_SUBVOLUME_NAME}/{directory.name}/"
        f"{values.BOOT_DIRECTORY_NAME}/{file_name}"
    )


def _kernel_version_key(version: str) -> tuple[int, ...]:
    """Order kernel versions by their numbers and not by their text."""

    return tuple(int(part) for part in re.findall(r"\d+", version))


def _search_line(root_spec: str) -> str:
    """The line that makes the boot loader use the device of the root.

    Which query the line carries follows the way the fstab names the device, so
    the entry works whether the machine names it by UUID, by label or by its
    device path.
    """

    if root_spec.startswith(values.UUID_SPEC_PREFIX):
        template = values.GRUB_SEARCH_UUID_LINE
        value = root_spec[len(values.UUID_SPEC_PREFIX) :]
    elif root_spec.startswith(values.LABEL_SPEC_PREFIX):
        template = values.GRUB_SEARCH_LABEL_LINE
        value = root_spec[len(values.LABEL_SPEC_PREFIX) :]
    else:
        template = values.GRUB_SEARCH_DEVICE_LINE
        value = root_spec
    return template.format(value=value)


def _root_spec() -> str | None:
    """Read the device field of the root line of the fstab."""

    try:
        text = setup_values.FSTAB_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    return fstab.spec_of_mount_point(text, str(setup_values.ROOT_MOUNT_POINT))


def _write_ignore_setting(warnings: list[str]) -> bool:
    """Keep the immutable point out of the generated list.

    The list is a setting of the generator of snapshot entries, and it may
    already carry points of an earlier run, so the setting is read, the point
    is added when it is missing, and every entry that is already there stays.
    """

    path = setup_values.GRUB_BTRFS_CONFIG_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read {path}: {exc}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False

    entry = f"{setup_values.POINTS_SUBVOLUME_NAME}/{values.POINT_NAME}"
    key = values.GRUB_BTRFS_IGNORE_KEY
    present = _ignore_entries(text, key)
    if entry in present:
        _log(f"the generator already ignores {entry}")
        return False

    directive = _ignore_directive((*present, entry))
    new_text, changed = replace_line_by_string(
        text, key, directive, fstab.COMMENT_SIGN
    )
    if not changed:
        _log(f"the generator already ignores {entry}")
        return False
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot write {path}: {exc}")
        _log(warnings[-1], priority=engine_values.ERROR_PRIORITY)
        return False
    _log(f"the generator now ignores {entry}")
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
    """Rebuild the boot menu, with the generator stopped around the rebuild.

    A rebuild that runs while the generator writes its list can leave the
    generated file missing, and the menu then loses the submenu of the points.
    Stopping the generator for the moment of the rebuild keeps the file in
    place.
    """

    unit = setup_values.GRUB_BTRFS_DAEMON_UNIT_NAME
    stopped = _systemctl(values.SYSTEMCTL_STOP_COMMAND, unit, warnings)
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
    finally:
        if stopped:
            _systemctl(values.SYSTEMCTL_START_COMMAND, unit, warnings)
    _log("boot menu rebuilt")
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
