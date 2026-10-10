"""Values of the btrfs_points_setup task.

The section stores the two things that make a recovery possible: the immutable
save point, which the user boots to return to a known good state, and the
writable copy of it, which the user works in and which writes to the disk like
any ordinary system. The point never changes, so it can be used for a recovery
again and again; the copy carries the daily work and can be thrown away and
recreated from the point at any time.

The point is named by a value and the copy by another one, because the names
appear in the boot menu and a person reads them there
(docs/spec/btrfs-setup.md).
"""

from __future__ import annotations

from pathlib import Path

# Packages the section installs from the Ubuntu archive. grub2-common carries
# the two tools of the boot loader the section asks for the device and for the
# path of a kernel: grub-probe reports the device of the filesystem that holds
# the kernels, and grub-mkrelpath writes the path of a file the way the boot
# loader itself names it. Both are the tools update-grub uses, so the entry the
# section writes names a kernel exactly as the machine's own boot menu does.
PACKAGES: tuple[str, ...] = ("grub2-common",)

# The two things the section stores in the points subvolume.
POINT_NAME: str = "Pyntara-permanent"
WORK_COPY_NAME: str = "Pyntara-work"

# Boot entry of the immutable point. The generator of snapshot entries applies
# one kernel parameter to every entry it writes, so the point cannot take its
# in-memory parameter from there; the entry is written by this section instead,
# as a script of the grub.d directory, which is the place update-grub reads
# entries from. The header and the body of one entry ship as files of the
# section under task_data, and the mode makes the script executable, which is
# what makes update-grub run it.
GRUB_D_ENTRY_PATH: Path = Path("/etc/grub.d/40_pyntara_permanent_entry")
GRUB_D_ENTRY_FILE_MODE: int = 0o755
GRUB_D_ENTRY_HEADER_FILE_NAME: str = "permanent_entry_header.in"
GRUB_D_ENTRY_BODY_FILE_NAME: str = "permanent_entry_body.in"
GRUB_D_ENTRY_ID: str = "pyntara-permanent"
GRUB_D_ENTRY_CLASS: str = "pyntara"

# Parameter that keeps the root filesystem in memory for a session started from
# the point. The point itself is never written to, so a session that went wrong
# leaves nothing behind and the same point serves the next recovery. Without
# this parameter a read-only point still boots, but nothing in it can write,
# which is a rescue session and not a working system.
OVERLAY_PARAMETER: str = "overlayroot=tmpfs:recurse=0"

# How the entry finds the device of the kernels. The device is the filesystem
# that carries the boot directory of the machine, and its UUID comes from the
# boot loader's own tool, so the entry names the device the way this machine
# names it and the entry is correct on every layout of the boot directory.
GRUB_SEARCH_UUID_LINE: str = "search --no-floppy --fs-uuid --set=root {value}"

# The two tools of the boot loader the section asks for the device and for the
# path of a kernel. grub-probe answers the UUID of the filesystem that carries a
# path, and grub-mkrelpath answers the path of a file the way the boot loader
# names it on that filesystem. Both are the tools update-grub uses, so the entry
# names a kernel exactly as the machine's own boot menu does, whether the boot
# directory is a separate partition, a subvolume of its own, or a directory
# inside the root subvolume.
GRUB_PROBE_UUID_COMMAND: tuple[str, ...] = (
    "grub-probe",
    "--target=fs_uuid",
    "{path}",
)
GRUB_MKRELPATH_COMMAND: tuple[str, ...] = ("grub-mkrelpath", "{path}")

# Mount point that carries the kernels of the machine. The section takes the
# kernels it writes entries for from here and not from inside the point: a
# machine whose boot directory is a partition or a subvolume of its own carries
# no kernel inside the point, because a snapshot does not cross a mount.
BOOT_MOUNT_POINT: Path = Path("/boot")

# The prefixes of a kernel and of its initial ramdisk, as the boot directory of
# the machine names them.
KERNEL_FILE_PREFIX: str = "vmlinuz-"
INITRD_FILE_PREFIX: str = "initrd.img-"

# The commands of the section: the snapshot of the point, the writable copy of
# it, the query that answers whether the point really is read only, the rebuild
# of the boot menu and the daemon that generates the entries of the points.
SNAPSHOT_COMMAND: tuple[str, ...] = (
    "btrfs",
    "subvolume",
    "snapshot",
    "{source}",
    "{target}",
)
READ_ONLY_SNAPSHOT_COMMAND: tuple[str, ...] = (
    "btrfs",
    "subvolume",
    "snapshot",
    "-r",
    "{source}",
    "{target}",
)
READ_ONLY_PROPERTY_COMMAND: tuple[str, ...] = (
    "btrfs",
    "property",
    "get",
    "{path}",
    "ro",
)

# The question that tells whether a path carries a subvolume, which is how the
# section knows that a point or a work copy is already stored there.
SUBVOLUME_SHOW_COMMAND: tuple[str, ...] = (
    "btrfs",
    "subvolume",
    "show",
    "{path}",
)
UPDATE_GRUB_COMMAND: tuple[str, ...] = ("update-grub",)
SYSTEMCTL_STOP_COMMAND: tuple[str, ...] = ("systemctl", "stop", "{unit}")
SYSTEMCTL_START_COMMAND: tuple[str, ...] = ("systemctl", "start", "{unit}")

# Setting of the generator that keeps the point out of the generated list. The
# menu then shows the point once, as the entry of this section with the
# parameter that keeps the root in memory, and it shows the writable copy as a
# generated entry, which boots the copy directly. The list is the shell array
# the generator reads, and its entries are the subvolume paths the generator
# itself lists, so the point is named without a leading slash.
GRUB_BTRFS_IGNORE_KEY: str = "GRUB_BTRFS_IGNORE_SPECIFIC_PATH"
GRUB_BTRFS_IGNORE_DIRECTIVE_FORMAT: str = "{key}=({entries})"
GRUB_BTRFS_IGNORE_ENTRY_FORMAT: str = '"{entry}"'

# Timeouts of the storage commands of the section and of the menu rebuild.
STORAGE_COMMAND_TIMEOUT_SECONDS: int = 120
SNAPSHOT_TIMEOUT_SECONDS: int = 600
UPDATE_GRUB_TIMEOUT_SECONDS: int = 300

# The value names this module declares.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "POINT_NAME",
    "WORK_COPY_NAME",
    "GRUB_D_ENTRY_PATH",
    "GRUB_D_ENTRY_FILE_MODE",
    "GRUB_D_ENTRY_HEADER_FILE_NAME",
    "GRUB_D_ENTRY_BODY_FILE_NAME",
    "GRUB_D_ENTRY_ID",
    "GRUB_D_ENTRY_CLASS",
    "OVERLAY_PARAMETER",
    "GRUB_SEARCH_UUID_LINE",
    "GRUB_PROBE_UUID_COMMAND",
    "GRUB_MKRELPATH_COMMAND",
    "BOOT_MOUNT_POINT",
    "KERNEL_FILE_PREFIX",
    "INITRD_FILE_PREFIX",
    "SNAPSHOT_COMMAND",
    "READ_ONLY_SNAPSHOT_COMMAND",
    "READ_ONLY_PROPERTY_COMMAND",
    "SUBVOLUME_SHOW_COMMAND",
    "UPDATE_GRUB_COMMAND",
    "SYSTEMCTL_STOP_COMMAND",
    "SYSTEMCTL_START_COMMAND",
    "GRUB_BTRFS_IGNORE_KEY",
    "GRUB_BTRFS_IGNORE_DIRECTIVE_FORMAT",
    "GRUB_BTRFS_IGNORE_ENTRY_FORMAT",
    "STORAGE_COMMAND_TIMEOUT_SECONDS",
    "SNAPSHOT_TIMEOUT_SECONDS",
    "UPDATE_GRUB_TIMEOUT_SECONDS",
)
