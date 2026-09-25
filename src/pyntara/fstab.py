"""Shared line helpers of the machine fstab.

Two sections read and change the fstab: btrfs_setup puts the compression option
on the lines of the mounted subvolumes and adds the line of the points
subvolume, and btrfs_points_setup reads the device field of the root line to
build the search line of the boot entry of the immutable point. The helpers live
here once, so both sections name one implementation
(docs/spec/btrfs-setup.md).

An fstab line carries one entry of six fields: the device, the mount point, the
filesystem type, the comma separated options, the dump field and the check
field. Only the first line that names a mount point is ever the line of that
mount point, and a comment line is never one.
"""

from __future__ import annotations

# Sign that marks a comment line of the fstab.
COMMENT_SIGN: str = "#"

# Number of fields a line of the fstab carries.
ENTRY_FIELD_COUNT: int = 6

# Position of the fields inside a line, counted from zero.
DEVICE_FIELD: int = 0
MOUNT_POINT_FIELD: int = 1
OPTIONS_FIELD: int = 3


def field_values(line: str) -> tuple[str, ...] | None:
    """Read the fields of one fstab line, or None for a line that is not one."""

    if line.lstrip().startswith(COMMENT_SIGN):
        return None
    fields = tuple(line.split())
    if len(fields) < ENTRY_FIELD_COUNT:
        return None
    return fields


def spec_of_mount_point(text: str, mount_point: str) -> str | None:
    """Read the device field of the line of one mount point."""

    for line in text.splitlines():
        fields = field_values(line)
        if fields is None or fields[MOUNT_POINT_FIELD] != mount_point:
            continue
        return fields[DEVICE_FIELD]
    return None


def carries_mount_point(text: str, mount_point: str) -> bool:
    """Answer whether the fstab already holds a line for one mount point."""

    return spec_of_mount_point(text, mount_point) is not None


def options_with_assignment(options_text: str, assignment: str) -> tuple[str, bool]:
    """Add or replace one option assignment in a comma separated option list.

    The option is named by the part of the assignment before the equals sign, so
    an option that is already there with another value is replaced instead of
    being added a second time. The answer is the new option list and whether it
    differs from the one that came in.
    """

    option_name = assignment.split("=", 1)[0]
    words = tuple(word for word in options_text.split(",") if word)
    replaced = tuple(
        assignment if word.split("=", 1)[0] == option_name else word
        for word in words
    )
    if not any(word.split("=", 1)[0] == option_name for word in replaced):
        replaced = (*replaced, assignment)
    new_options = ",".join(replaced)
    return new_options, new_options != options_text


def text_with_option(
    text: str, mount_point: str, assignment: str
) -> tuple[str, bool]:
    """Return the fstab text with one option on the line of one mount point.

    Only the options field of that line changes: the device, the mount point,
    the filesystem type and the check fields stay as the machine wrote them, and
    the first line that names the mount point is the one that changes.
    """

    lines = text.splitlines()
    changed = False
    for index, line in enumerate(lines):
        fields = field_values(line)
        if fields is None or fields[MOUNT_POINT_FIELD] != mount_point:
            continue
        new_options, option_changed = options_with_assignment(
            fields[OPTIONS_FIELD], assignment
        )
        if option_changed:
            changed_fields = list(fields)
            changed_fields[OPTIONS_FIELD] = new_options
            lines[index] = " ".join(changed_fields)
            changed = True
        break
    result = "\n".join(lines)
    if text.endswith("\n") or changed:
        result += "\n"
    return result, changed
