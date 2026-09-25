"""Values of the btrfs_setup task.

The section readies the btrfs filesystem of the machine: it installs the tools,
turns on the compression of the mounted system subvolumes, creates and mounts
the top level subvolume that holds the save points, writes the maintenance
schedule and prepares the boot menu that shows the points
(docs/spec/btrfs-setup.md).

Every value of the section lives here, so the task body carries no literal of
its own and a machine reads exactly what the repository declares.
"""

from __future__ import annotations

from pathlib import Path

# Packages the section installs from the Ubuntu archive. btrfs-progs carries
# the btrfs command, btrfsmaintenance the maintenance timers, btrfs-compsize
# the compsize report, augeas-tools the augtool a task edits configuration
# with, overlayroot the initramfs hook that runs the immutable point with the
# root filesystem in memory, inotify-tools the inotifywait the menu daemon
# needs to notice a new point, and make builds the menu generator from its
# pinned sources.
PACKAGES: tuple[str, ...] = (
    "btrfs-progs",
    "btrfsmaintenance",
    "btrfs-compsize",
    "augeas-tools",
    "overlayroot",
    "inotify-tools",
    "make",
)

# Command that reports one mount point as SOURCE, FSTYPE and OPTIONS; the
# section reads the filesystem type, the device, the mounted subvolume and the
# mounted options from it. The placeholders are filled by the task.
FINDMNT_COMMAND: tuple[str, ...] = (
    "findmnt",
    "--noheadings",
    "--output",
    "SOURCE,FSTYPE,OPTIONS",
    "--target",
    "{mount_point}",
)

# Mount point whose filesystem decides whether the section has work to do.
ROOT_MOUNT_POINT: Path = Path("/")

# Filesystem type the whole section applies to. Any other type costs the
# section a warning and nothing else, because a machine without btrfs stays a
# usable machine.
BTRFS_FILESYSTEM_TYPE: str = "btrfs"

# Compression the machine writes new data with, in the form the fstab option
# list carries it.
COMPRESSION_OPTION_ASSIGNMENT: str = "compress=zstd:15"

# Mount points whose fstab line receives the compression option. The option is
# turned on before the bulk of the install runs, so everything the run writes
# is already compressed.
COMPRESSED_MOUNT_POINTS: tuple[str, ...] = ("/", "/home")

# Command that applies the fstab options of a mounted filesystem again, so the
# new compression option is in force without a reboot.
REMOUNT_COMMAND: tuple[str, ...] = ("mount", "-o", "remount", "{mount_point}")

# The fstab the mount options and the points line are written to.
FSTAB_PATH: Path = Path("/etc/fstab")

# Name of the top level subvolume that holds the save points and the mount
# point it appears at. The subvolume sits at the top level of the filesystem,
# because a point stored inside the root subvolume would travel away with the
# root when the root is renamed.
POINTS_SUBVOLUME_NAME: str = "@points"
POINTS_MOUNT_POINT: Path = Path("/points")

# Name of the top level subvolume that holds the swap area. The kernel refuses
# to snapshot a subvolume that carries an active swap file of this filesystem,
# and it refuses to activate a swap file whose extents a snapshot shares, so a
# swap file inside the root subvolume either blocks the save point or is left
# dead by it. The kernel names both refusals in its journal: "cannot snapshot
# subvolume with active swapfile" and "swapfile must not be copy-on-write".
# The directory the subvolume is mounted at is the directory of the swap file,
# read from the values of the swap section, so the path is declared once.
SWAP_SUBVOLUME_NAME: str = "@swap"

# fstab line of a subvolume mount. The specification of the line is taken from
# the root line of the same fstab, so the task copies no UUID and the line names
# the device the way this machine names it. nofail keeps a machine whose
# subvolume is missing bootable.
SUBVOLUME_FSTAB_LINE_FORMAT: str = (
    "{spec} {mount_point} {filesystem_type} "
    "subvol={subvolume},defaults,noatime,nofail 0 0"
)

# Mount that reaches the top level of the filesystem, where the subvolumes are
# created, and the temporary mount point it uses. The directory lives under
# /run, the runtime directory the project already owns.
TOPLEVEL_SUBVOLUME_ID: str = "5"
TOPLEVEL_MOUNT_COMMAND: tuple[str, ...] = (
    "mount",
    "-o",
    "subvolid={subvolume_id}",
    "{device}",
    "{mount_point}",
)
TOPLEVEL_UNMOUNT_COMMAND: tuple[str, ...] = ("umount", "{mount_point}")
TOPLEVEL_MOUNT_POINT: Path = Path("/run/pyntara/btrfs-toplevel")
TOPLEVEL_DIRECTORY_MODE: int = 0o755

# Subcommands of btrfs the section runs, with the placeholders the task fills:
# the listing that tells whether a subvolume is already there, the creation of
# one subvolume and the mount that appears at its mount point.
SUBVOLUME_LIST_COMMAND: tuple[str, ...] = ("btrfs", "subvolume", "list", "{path}")
SUBVOLUME_CREATE_COMMAND: tuple[str, ...] = ("btrfs", "subvolume", "create", "{path}")
MOUNT_COMMAND: tuple[str, ...] = ("mount", "{mount_point}")

# Timeout of the short storage commands of the section and of its systemctl
# calls.
STORAGE_COMMAND_TIMEOUT_SECONDS: int = 120

# The menu generator grub-btrfs: the pinned commit its sources are taken from,
# the archive, the directory it is built in and the build itself. The generator
# is not in the Ubuntu archive, so the section builds it from these sources.
GRUB_BTRFS_COMMIT: str = "38cd2fa419e4c1c0f1e345a374b37c040c170047"
GRUB_BTRFS_SOURCE_URL: str = (
    "https://github.com/Antynea/grub-btrfs/archive/{commit}.tar.gz"
)
GRUB_BTRFS_ARCHIVE_FILE_NAME: str = "grub-btrfs-{commit}.tar.gz"
GRUB_BTRFS_SOURCE_DIRECTORY_NAME: str = "grub-btrfs-{commit}"
GRUB_BTRFS_BUILD_DIRECTORY: Path = Path("/usr/local/src/pyntara-grub-btrfs")
GRUB_BTRFS_BUILD_DIRECTORY_MODE: int = 0o755
GRUB_BTRFS_EXTRACT_COMMAND: tuple[str, ...] = (
    "tar",
    "-xf",
    "{archive}",
    "-C",
    "{directory}",
)
GRUB_BTRFS_BUILD_COMMAND: tuple[str, ...] = (
    "make",
    "-C",
    "{source_directory}",
    "install",
)
GRUB_BTRFS_BUILD_TIMEOUT_SECONDS: int = 900

# The file the build installs. Its presence means the generator is installed,
# so a rerun skips the download and the build.
GRUB_BTRFS_INSTALLED_PATH: Path = Path("/etc/grub.d/41_snapshots-btrfs")

# Configuration of the generator, the key of the kernel parameters its entries
# carry, and the line the section writes there: empty, so every entry the
# generator writes boots its subvolume directly and writes to the disk. The
# immutable point needs the root filesystem in memory instead, and its own
# entry carries that parameter itself.
GRUB_BTRFS_CONFIG_PATH: Path = Path("/etc/default/grub-btrfs/config")
GRUB_BTRFS_KERNEL_PARAMETERS_KEY: str = "GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS"
GRUB_BTRFS_KERNEL_PARAMETERS_DIRECTIVE: str = (
    'GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS=""'
)

# The daemon that watches the points and regenerates the snapshot list: its
# path, its unit, and the drop-in that points the watch at the points mount and
# orders the unit after that mount. The watch is recursive, because a change
# inside a work copy is a change the line of that copy should follow; it did not
# on its own in the live probes, so the points section rebuilds the menu itself
# after a change instead of relying on the daemon for that. The drop-in body
# ships as a file of the section.
GRUB_BTRFS_DAEMON_PATH: Path = Path("/usr/bin/grub-btrfsd")
GRUB_BTRFS_DAEMON_UNIT_NAME: str = "grub-btrfsd.service"
GRUB_BTRFS_DAEMON_DROPIN_PATH: Path = Path(
    "/etc/systemd/system/grub-btrfsd.service.d/watch-points.conf"
)
GRUB_BTRFS_DAEMON_DROPIN_FILE_NAME: str = "grub-btrfsd-watch-points.conf"
GRUB_BTRFS_DAEMON_WATCH_OPTION: str = "--recursive"
GRUB_BTRFS_DAEMON_SYSLOG_OPTION: str = "--syslog"
GRUB_BTRFS_DAEMON_DROPIN_FILE_MODE: int = 0o644

# Maintenance of the filesystem: the configuration file of btrfsmaintenance,
# the lines the section owns there, and the timers it turns on and off. The
# lines cover every setting the task declares, so a machine that carries the
# file with other values receives the declared ones. Defragmentation and the
# periodic trim stay off: the one-off recompression task owns defragmentation,
# and a trim of an SSD happens at the settings of the device itself.
MAINTENANCE_CONFIG_PATH: Path = Path("/etc/default/btrfsmaintenance")
MAINTENANCE_DIRECTIVES: tuple[str, ...] = (
    'BTRFS_LOG_OUTPUT="journal"',
    'BTRFS_DEFRAG_PERIOD="none"',
    'BTRFS_DEFRAG_PATHS=""',
    'BTRFS_TRIM_PERIOD="none"',
    'BTRFS_SCRUB_PERIOD="Sun *-*-* 03:00:00"',
    'BTRFS_SCRUB_MOUNTPOINTS="/"',
    'BTRFS_SCRUB_PRIORITY="idle"',
    'BTRFS_BALANCE_PERIOD="Tue *-*-* 03:30:00"',
    'BTRFS_BALANCE_MOUNTPOINTS="/"',
    'BTRFS_BALANCE_DUSAGE="0 73"',
    'BTRFS_BALANCE_MUSAGE="5"',
)
MAINTENANCE_ENABLED_TIMERS: tuple[str, ...] = (
    "btrfs-scrub.timer",
    "btrfs-balance.timer",
)
MAINTENANCE_DISABLED_TIMERS: tuple[str, ...] = (
    "btrfs-defrag.timer",
    "btrfs-trim.timer",
)

# systemctl calls of the section, each carrying the unit name where it needs
# one. The swap calls serve the move of the swap area into its subvolume: the
# swap file has to be inactive before it can be removed from the root
# subvolume, and the swap service starts again once the subvolume is mounted.
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = ("systemctl", "enable", "--now", "{unit}")
SYSTEMCTL_DISABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "disable",
    "--now",
    "{unit}",
)
SYSTEMCTL_RESTART_COMMAND: tuple[str, ...] = ("systemctl", "restart", "{unit}")
SYSTEMCTL_STOP_COMMAND: tuple[str, ...] = ("systemctl", "stop", "{unit}")
SYSTEMCTL_START_COMMAND: tuple[str, ...] = ("systemctl", "start", "{unit}")

# The value names this module declares, so the values guard of the test suite
# applies its rules to every one of them.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "FINDMNT_COMMAND",
    "ROOT_MOUNT_POINT",
    "BTRFS_FILESYSTEM_TYPE",
    "COMPRESSION_OPTION_ASSIGNMENT",
    "COMPRESSED_MOUNT_POINTS",
    "REMOUNT_COMMAND",
    "FSTAB_PATH",
    "POINTS_SUBVOLUME_NAME",
    "POINTS_MOUNT_POINT",
    "SWAP_SUBVOLUME_NAME",
    "SUBVOLUME_FSTAB_LINE_FORMAT",
    "TOPLEVEL_SUBVOLUME_ID",
    "TOPLEVEL_MOUNT_COMMAND",
    "TOPLEVEL_UNMOUNT_COMMAND",
    "TOPLEVEL_MOUNT_POINT",
    "TOPLEVEL_DIRECTORY_MODE",
    "SUBVOLUME_LIST_COMMAND",
    "SUBVOLUME_CREATE_COMMAND",
    "MOUNT_COMMAND",
    "STORAGE_COMMAND_TIMEOUT_SECONDS",
    "GRUB_BTRFS_COMMIT",
    "GRUB_BTRFS_SOURCE_URL",
    "GRUB_BTRFS_ARCHIVE_FILE_NAME",
    "GRUB_BTRFS_SOURCE_DIRECTORY_NAME",
    "GRUB_BTRFS_BUILD_DIRECTORY",
    "GRUB_BTRFS_BUILD_DIRECTORY_MODE",
    "GRUB_BTRFS_EXTRACT_COMMAND",
    "GRUB_BTRFS_BUILD_COMMAND",
    "GRUB_BTRFS_BUILD_TIMEOUT_SECONDS",
    "GRUB_BTRFS_INSTALLED_PATH",
    "GRUB_BTRFS_CONFIG_PATH",
    "GRUB_BTRFS_KERNEL_PARAMETERS_KEY",
    "GRUB_BTRFS_KERNEL_PARAMETERS_DIRECTIVE",
    "GRUB_BTRFS_DAEMON_PATH",
    "GRUB_BTRFS_DAEMON_UNIT_NAME",
    "GRUB_BTRFS_DAEMON_DROPIN_PATH",
    "GRUB_BTRFS_DAEMON_DROPIN_FILE_NAME",
    "GRUB_BTRFS_DAEMON_WATCH_OPTION",
    "GRUB_BTRFS_DAEMON_SYSLOG_OPTION",
    "GRUB_BTRFS_DAEMON_DROPIN_FILE_MODE",
    "MAINTENANCE_CONFIG_PATH",
    "MAINTENANCE_DIRECTIVES",
    "MAINTENANCE_ENABLED_TIMERS",
    "MAINTENANCE_DISABLED_TIMERS",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
    "SYSTEMCTL_DISABLE_COMMAND",
    "SYSTEMCTL_RESTART_COMMAND",
    "SYSTEMCTL_STOP_COMMAND",
    "SYSTEMCTL_START_COMMAND",
)
