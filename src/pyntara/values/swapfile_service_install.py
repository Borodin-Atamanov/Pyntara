"""Values of the swapfile_service_install task.

The section deploys one program and the boot service that runs it. The program
creates the swap file at the size min(RAM * ram_multiplier + ram_extra_mb,
free_disk * disk_fraction), formats it, activates it, and refuses storage that
keeps its data in memory, so the size formula and the commands of the swap
itself live in the program alone and the task carries only what it must pass,
where to put things and which packages its tools come from
(docs/spec/users-and-host.md).

The kernel file the installed memory is read from and the name of the line
inside it that carries the total are read from the shared module, because the
zram_service section reads the same file and line.
"""

from __future__ import annotations

from pathlib import Path

# Path to the swap file. The directory that holds it is created by the program.
SWAPFILE_PATH: Path = Path("/swap/swapfile")

# Multiplier applied to installed RAM to get the base swap size.
RAM_MULTIPLIER: float = 1.6

# Extra mebibytes added to the RAM-derived swap size before the disk cap.
RAM_EXTRA_MB: int = 4096

# Maximum share of free disk space the swap file may occupy, 0.0 to 1.0.
DISK_FRACTION: float = 0.5

# File mode of the created swapfile.
SWAPFILE_MODE: int = 0o600

# Accepted deviation between the existing and the target swap size, in
# mebibytes; a swapfile within the tolerance is not recreated.
SIZE_TOLERANCE_MB: int = 1

# Size of the file the program tries the storage with, in kibibytes. The probe
# is created next to the swap file and removed again, so storage that keeps its
# data in memory costs a few kibibytes instead of the size of the swap file.
PROBE_SIZE_KB: int = 512

# The packages the tools of the program come from: swapon and swapoff are in
# mount, mkswap and fallocate in util-linux, chattr in e2fsprogs. The task
# installs them through the shared package helper, so the section never assumes
# the machine already carries them.
PACKAGES: tuple[str, ...] = ("mount", "util-linux", "e2fsprogs")

# The program of the section: the file of the clone under task_data, the path it
# is deployed to and the mode that makes it executable. The name carries the
# project, because the deployed directory holds the commands of several
# sections.
PROGRAM_FILE_NAME: str = "configure_swapfile.py"
PROGRAM_DEPLOY_PATH: Path = Path("/usr/local/bin/pyntara-swapfile")
PROGRAM_FILE_MODE: int = 0o755

# Name of the systemd oneshot service unit that runs the program at boot.
SERVICE_UNIT_NAME: str = "swapfile.service"

# Name of the unit template under task_data/swapfile_service_install/ of the
# clone; the run renders it with the command line of the program and the
# swapfile path.
UNIT_TEMPLATE_FILE_NAME: str = "swapfile.service"

# systemctl calls of the task, each carrying the unit name as its placeholder.
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)
SYSTEMCTL_START_COMMAND: tuple[str, ...] = (
    "systemctl",
    "start",
    "{service_unit_name}",
)

# Name of the tool the unit stops the swap with. The task resolves its absolute
# path at run time and renders it into the unit, so no path has to be kept in
# step by hand and the boot service takes nothing from PATH.
SWAPOFF_COMMAND_NAME: str = "swapoff"

# The swap tool that lists every active swap area with its kind, one line per
# area as the name and the type. The listing is how the section tells a swap
# file from a swap device: only a line whose type is the file type below names a
# file this section may remove, while a partition or a zram device is left alone.
SWAP_SHOW_COMMAND: tuple[str, ...] = (
    "swapon",
    "--show",
    "--noheadings",
    "--output",
    "NAME,TYPE",
)
SWAP_FILE_TYPE: str = "file"

# The address a hibernation image is resumed from. The kernel reads the device
# from the kernel command line of the boot entry and the offset of the swap file
# header inside that device from the same line, because the kernel ignores the
# offset of the resume parameter and a device alone does not name a swap file.
# The device is written the way the fstab of this machine names the mount that
# holds the swap file: a UUID for a filesystem that is mounted directly, and the
# mapper path of a root that is unlocked at boot, which is the name the
# initramfs can use once that root is there (docs/spec/users-and-host.md).
FSTAB_PATH: Path = Path("/etc/fstab")
GRUB_DEFAULT_FILE_PATH: Path = Path("/etc/default/grub")
GRUB_CONFIG_FILE_PATH: Path = Path("/boot/grub/grub.cfg")
GRUB_COMMAND_LINE_KEY: str = "GRUB_CMDLINE_LINUX_DEFAULT"
RESUME_DEVICE_PARAMETER: str = "resume"
RESUME_OFFSET_PARAMETER: str = "resume_offset"

# The fstab of the machine is not a shell variable file, so the section parses
# and writes it with its own lens when it removes a swap entry of another owner.
# An entry that activates a swap area names the area in its device field and
# carries the type swap; only an entry whose device field is a path names a swap
# file, while an entry whose device field is a UUID or a device node names a
# device the section never removes.
FSTAB_LENS: str = "Fstab.lns"
FSTAB_SWAP_TYPE: str = "swap"
FSTAB_SWAP_MOUNT_POINTS: tuple[str, ...] = ("none", "swap")
FSTAB_SWAP_REMOVE_LINE: str = (
    "rm {node}/*[vfstype='{swap_type}'][spec='{spec}']"
)

# The lens augeas parses the machine settings files with, and the key the
# initramfs reads the resume device from. Both settings files are shell variable
# files, so augeas parses each of them with the lens named here and the section
# never edits the syntax of a machine file itself: it replaces one node of the
# parsed tree and the tool writes the file back, which keeps every other line,
# every comment and the quoting style of the file as the machine wrote them
# (docs/spec/users-and-host.md). The lens is Sysconfig and not Shellvars,
# because Shellvars cannot write back a file that carries an empty quoted value
# such as GRUB_CMDLINE_LINUX="": it answers put_failed on save, so the resume
# address is then never published (measured on Kubuntu 26.04).
AUGEAS_LENS: str = "Sysconfig.lns"
INITRAMFS_RESUME_KEY: str = "RESUME"

# The mount point a path belongs to, which is how the task reaches the fstab
# line whose device field names the filesystem that holds the swap file.
MOUNT_POINT_COMMAND: tuple[str, ...] = (
    "findmnt",
    "--noheadings",
    "--output",
    "TARGET",
    "--target",
    "{path}",
)

# The file the initramfs reads the resume device from. The initramfs reads the
# files of this directory before it reads the kernel command line, so the
# command line decides where the two disagree; the file is written as well, so a
# machine whose command line a person edited by hand still carries the device.
INITRAMFS_RESUME_FILE_PATH: Path = Path("/etc/initramfs-tools/conf.d/resume")

# The directory the resume file is copied into inside the initial ramdisk, and
# the command that lists what one image carries. The listing is how the task
# reads back that the rebuilt image really holds the file, instead of trusting
# that the tool copied it.
INITRAMFS_CONF_DIRECTORY_IN_IMAGE: str = "conf/conf.d"
INITRAMFS_IMAGE_LIST_COMMAND: tuple[str, ...] = (
    "lsinitramfs",
    "/boot/initrd.img-{kernel_release}",
)

# The attributes the running kernel takes the resume address through, which is
# how the initramfs hands it over at boot. Writing them is what makes the
# session that installs the swap file agree with the boots that follow instead
# of waiting for the next boot, and they are the two values logind reads before
# it offers hibernation.
POWER_RESUME_FILE_PATH: Path = Path("/sys/power/resume")
POWER_RESUME_OFFSET_FILE_PATH: Path = Path("/sys/power/resume_offset")

# The commands that rebuild the artifacts of the boot, each with its bound: the
# boot menu carries the address and the initial ramdisk carries the file the
# initramfs reads.
UPDATE_GRUB_TIMEOUT_SECONDS: int = 300
UPDATE_INITRAMFS_COMMAND: tuple[str, ...] = ("update-initramfs", "-u")
UPDATE_INITRAMFS_TIMEOUT_SECONDS: int = 600

# The permission hibernation needs. Ubuntu refuses it to every user through a
# rule of its own, and polkit decides by the first rule file in name order that
# answers, so this file has to sort before com.ubuntu.desktop.rules of that
# distribution, which a name starting with a digit does. The rule names the
# desktop user alone, so no other account of the machine gains the permission,
# and it allows the two actions the session menu of that user asks for.
POLKIT_RULE_FILE_PATH: Path = Path(
    "/etc/polkit-1/rules.d/49-pyntara-hibernate.rules"
)
POLKIT_RULE_FILE_MODE: int = 0o644
POLKIT_RULE_TEMPLATE_FILE_NAME: str = "hibernate.rules.in"
POLKIT_HIBERNATE_ACTION: str = "org.freedesktop.login1.hibernate"
POLKIT_HIBERNATE_MULTIPLE_SESSIONS_ACTION: str = (
    "org.freedesktop.login1.hibernate-multiple-sessions"
)

# The question the task asks about hibernation on behalf of the desktop user,
# and the answer that means the machine offers it. The question is the
# CanHibernate call of logind, whose answer is calculated for the account that
# asks, which is why the call runs as that user instead of as root: a machine
# that refuses the user answers root with yes, and that state is exactly the one
# this section removes. The call is a method and not a property on systemd 259,
# which is the version of the target distribution.
LOGIND_HIBERNATE_QUERY_COMMAND: tuple[str, ...] = (
    "busctl",
    "--system",
    "call",
    "org.freedesktop.login1",
    "/org/freedesktop/login1",
    "org.freedesktop.login1.Manager",
    "CanHibernate",
)
HIBERNATE_AVAILABLE_ANSWER: str = "yes"

# The sentence the run leaves for the user. Plasma asks the machine once, when
# the session starts, so a machine whose hibernation this run enabled shows the
# item of its menu after the next login and not in the session that installed
# it.
SESSION_RELOAD_MESSAGE: str = (
    "log in again for the hibernation item of the session menu to appear"
)

# The names the task reads. The list lives next to the values it names and is
# read by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "SWAPFILE_PATH",
    "RAM_MULTIPLIER",
    "RAM_EXTRA_MB",
    "DISK_FRACTION",
    "SWAPFILE_MODE",
    "SIZE_TOLERANCE_MB",
    "PROBE_SIZE_KB",
    "PACKAGES",
    "PROGRAM_FILE_NAME",
    "PROGRAM_DEPLOY_PATH",
    "PROGRAM_FILE_MODE",
    "SERVICE_UNIT_NAME",
    "UNIT_TEMPLATE_FILE_NAME",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
    "SYSTEMCTL_START_COMMAND",
    "SWAPOFF_COMMAND_NAME",
    "SWAP_SHOW_COMMAND",
    "SWAP_FILE_TYPE",
    "FSTAB_PATH",
    "GRUB_DEFAULT_FILE_PATH",
    "GRUB_CONFIG_FILE_PATH",
    "GRUB_COMMAND_LINE_KEY",
    "RESUME_DEVICE_PARAMETER",
    "RESUME_OFFSET_PARAMETER",
    "FSTAB_LENS",
    "FSTAB_SWAP_TYPE",
    "FSTAB_SWAP_MOUNT_POINTS",
    "FSTAB_SWAP_REMOVE_LINE",
    "AUGEAS_LENS",
    "INITRAMFS_RESUME_KEY",
    "MOUNT_POINT_COMMAND",
    "INITRAMFS_RESUME_FILE_PATH",
    "INITRAMFS_CONF_DIRECTORY_IN_IMAGE",
    "INITRAMFS_IMAGE_LIST_COMMAND",
    "POWER_RESUME_FILE_PATH",
    "POWER_RESUME_OFFSET_FILE_PATH",
    "UPDATE_GRUB_TIMEOUT_SECONDS",
    "UPDATE_INITRAMFS_COMMAND",
    "UPDATE_INITRAMFS_TIMEOUT_SECONDS",
    "POLKIT_RULE_FILE_PATH",
    "POLKIT_RULE_FILE_MODE",
    "POLKIT_RULE_TEMPLATE_FILE_NAME",
    "POLKIT_HIBERNATE_ACTION",
    "POLKIT_HIBERNATE_MULTIPLE_SESSIONS_ACTION",
    "LOGIND_HIBERNATE_QUERY_COMMAND",
    "HIBERNATE_AVAILABLE_ANSWER",
    "SESSION_RELOAD_MESSAGE",
)
