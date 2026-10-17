"""Values of the tmp_to_disk task.

The task moves the temporary directory of a desktop machine from memory to the
disk: it copies what the running memory mount holds, removes the fstab entry
that asks for that mount, masks the unit systemd starts at boot to provide it,
and releases the mount of the running session. The values here name the machine
paths, the lens that edits the fstab, the unit and its systemctl commands, the
copy tool and the staging directory that keeps the data while the memory mount
is still in place.
"""

from __future__ import annotations

from pathlib import Path

# Mount point of the temporary directory, in memory and on the disk alike.
TMP_MOUNT_POINT: Path = Path("/tmp")

# The machine fstab whose entry asks for a memory temporary directory.
FSTAB_PATH: Path = Path("/etc/fstab")

# Lens augeas parses the fstab with. The fstab is not a shell variable file, so
# the section edits it with its own lens and never writes the syntax itself.
FSTAB_LENS: str = "Fstab.lns"

# Selector of the fstab entries this task removes: an entry whose mount point
# field and filesystem type field name the memory temporary directory. The
# selectors of the fstab lens are the field names of an entry, so only a line
# that really mounts the temporary directory from memory is ever matched.
FSTAB_ENTRY_SELECTOR: str = "*[file='{mount_point}'][vfstype='{filesystem_type}']"

# Line of the augtool program that removes the selected entries.
FSTAB_REMOVE_LINE: str = "rm {node}"

# Filesystem type of the memory mount this task moves to the disk.
TMPFS_TYPE: str = "tmpfs"

# Unit that mounts the temporary directory from memory at every boot. Masking
# it is what keeps the next boot on the disk, because the unit carries no
# [Install] section and a disable therefore cannot reach it.
SERVICE_UNIT_NAME: str = "tmp.mount"

# Word systemctl answers for a masked unit, which is the target state.
UNIT_MASKED_STATE: str = "masked"

# Command that masks the unit, reversible with systemctl unmask.
SYSTEMCTL_MASK_COMMAND: tuple[str, ...] = ("systemctl", "mask", "{unit}")

# Query of the filesystem type of one mount point. A path that is not a mount
# point answers with no line and a nonzero status, which reads as "not mounted".
MOUNT_TYPE_COMMAND: tuple[str, ...] = (
    "findmnt",
    "--noheadings",
    "--output",
    "FSTYPE",
    "{path}",
)

# Release of a mount that a running process still holds. A unit stop runs a
# regular unmount, and the kernel refuses that while any process keeps a working
# directory or an open file there, as a long-lived daemon does. The lazy
# unmount detaches the mount from the namespace at once, so the path shows the
# disk directory, and the old memory mount disappears when its last user lets
# go. The tool option is the documented answer of util-linux to a busy mount.
UMOUNT_COMMAND: tuple[str, ...] = ("umount", "--lazy", "{path}")

# Directory on the disk that holds the copy of the live temporary directory
# while the memory mount is still in place, and the prefix of the copy the task
# creates there. The whole copy is removed once its contents reach the disk.
STAGING_DIRECTORY_PATH: Path = Path("/var/tmp")
STAGING_DIRECTORY_PREFIX: str = "pyntara-tmp-to-disk-"

# Mode of that copy: readable by root alone, because it holds whatever the
# session left in the temporary directory.
STAGING_DIRECTORY_MODE: int = 0o700

# Mode of the temporary directory on the disk: world writable with the sticky
# bit, the mode the memory mount carried and every program expects.
TMP_DIRECTORY_MODE: int = 0o1777

# Mask that keeps the permission bits of a file mode and drops the special
# bits, so a mode read back from the machine compares with the declared one.
DIRECTORY_PERMISSION_MASK: int = 0o7777

# Copy tool and its flags: regular files, directories and symbolic links with
# their permission, ownership, timestamp, ACL and extended attribute metadata,
# hard links kept, numeric ids used. Special files such as sockets are skipped
# on purpose: a memory mount holds the sockets of the running session, and they
# mean nothing on the disk.
COPY_COMMAND: tuple[str, ...] = (
    "rsync",
    "--recursive",
    "--links",
    "--perms",
    "--times",
    "--group",
    "--owner",
    "--acls",
    "--xattrs",
    "--hard-links",
    "--numeric-ids",
)

# Tools the task needs on the target machine: augtool edits the fstab, rsync
# copies the data. The task installs them itself, so it depends on no other
# task.
PACKAGES: tuple[str, ...] = ("augeas-tools", "rsync")

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "TMP_MOUNT_POINT",
    "FSTAB_PATH",
    "FSTAB_LENS",
    "FSTAB_ENTRY_SELECTOR",
    "FSTAB_REMOVE_LINE",
    "TMPFS_TYPE",
    "SERVICE_UNIT_NAME",
    "UNIT_MASKED_STATE",
    "SYSTEMCTL_MASK_COMMAND",
    "MOUNT_TYPE_COMMAND",
    "UMOUNT_COMMAND",
    "STAGING_DIRECTORY_PATH",
    "STAGING_DIRECTORY_PREFIX",
    "STAGING_DIRECTORY_MODE",
    "TMP_DIRECTORY_MODE",
    "DIRECTORY_PERMISSION_MASK",
    "COPY_COMMAND",
    "PACKAGES",
)
