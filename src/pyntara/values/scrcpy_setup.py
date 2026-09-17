"""Values of the scrcpy_setup task.

The values of the scrcpy section as typed constants: the section describes
the scrcpy client installed for the desktop user, whose primary source is the
newest release of the configured repository and whose fallback is the Ubuntu
archive (docs/spec/scrcpy-setup.md).

A value another task needs as well lives in the shared module; the desktop
user and the package budgets are read from there.
"""

from __future__ import annotations

from pathlib import Path

# Owner and name pair of the release repository.
GITHUB_REPO: str = "Genymobile/scrcpy"

# Name of the release archive with {asset_arch} (the release spelling of the
# dpkg architecture, mapped by the engine release_asset_architectures) and
# {release_tag} substituted. The name is the only thing assumed about the
# archive: the directory inside it is discovered after extraction, so a
# release whose layout changes still installs.
ARCHIVE_NAME_TEMPLATE: str = "scrcpy-linux-{asset_arch}-{release_tag}.tar.gz"

# Checksum file published in the same release, next to the archive.
CHECKSUM_FILE_NAME: str = "SHA256SUMS.txt"

# Packages of the fallback source: the distribution client, installed only
# when the release path is unavailable.
FALLBACK_PACKAGES: tuple[str, ...] = ("scrcpy",)

# Android USB rules package of the Ubuntu archive. The release archive carries
# no udev rules, and without them a desktop user cannot reach a device over the
# cable; the package is installed when it is missing in both paths, and a
# failure is reported with what will not work.
UDEV_RULES_PACKAGE_NAME: str = "android-udev-rules"

# Client binary the Ubuntu archive installs, used for the menu entry and for
# the acceptance check when the fallback path was taken.
APT_BINARY_PATH: Path = Path("/usr/bin/scrcpy")

# Icon name the desktop resolves through the icon theme; used when the release
# tree is not installed, because the distribution places its icon in the system
# theme instead of the install directory.
THEME_ICON_NAME: str = "scrcpy"

# Root cache that keeps the archive of the installed release.
DOWNLOAD_DIR: Path = Path("/var/cache/pyntara/scrcpy")

# Relative path of the install directory under the user home; every release
# lands in a directory named after its version inside it, so a new version
# never overwrites a working one.
INSTALL_DIR_RELATIVE_PATH: str = ".local/share/scrcpy"

# Relative path of the command in the user prefix: a symbolic link to the
# client inside the version directory, switched only after the new client
# answered its version query.
COMMAND_RELATIVE_PATH: str = ".local/bin/scrcpy"

# Relative paths of the two menu entries under the user home.
LAUNCHER_RELATIVE_PATH: str = ".local/share/applications/scrcpy.desktop"
CONSOLE_LAUNCHER_RELATIVE_PATH: str = (
    ".local/share/applications/scrcpy-console.desktop"
)

# Menu entry templates under task_data/scrcpy_setup/ of the clone, with
# $binary and $icon as their placeholders. The console entry exists because the
# client prints the reason it cannot reach a device and the window would
# otherwise close before the user reads it.
LAUNCHER_TEMPLATE_FILE_NAME: str = "scrcpy.desktop"
CONSOLE_LAUNCHER_TEMPLATE_FILE_NAME: str = "scrcpy-console.desktop"

# File names inside the release archive, inside the version directory and, for
# the icon, inside the entry substitution.
BINARY_FILE_NAME: str = "scrcpy"
SERVER_FILE_NAME: str = "scrcpy-server"
ADB_FILE_NAME: str = "adb"
ICON_FILE_NAME: str = "scrcpy.png"

# Prefix of the temporary directory the archive is extracted into.
EXTRACT_DIR_PREFIX: str = "pyntara-scrcpy-"

# Relative path of the user trash; a superseded version directory is moved
# there, never deleted.
TRASH_DIR_RELATIVE_PATH: str = ".local/share/Trash/files"

# Client version query, with the client path as its placeholder. The answer
# proves that the delivered artifact runs on this machine; the version the task
# compares for idempotency is read from the symbolic link instead.
VERSION_COMMAND: tuple[str, ...] = ("{binary}", "--version")

# Checksum query, with the file as its placeholder: the printed digest is
# compared with the line of the published checksum file that names the archive.
# sha256sum reports a file it cannot read as a nonzero exit.
CHECKSUM_COMMAND: tuple[str, ...] = ("sha256sum", "{file}")

# Archive extraction, with the archive and the directory as its placeholders.
ARCHIVE_EXTRACT_COMMAND: tuple[str, ...] = (
    "tar",
    "--extract",
    "--gzip",
    "--file",
    "{archive}",
    "--directory",
    "{extract_dir}",
)

# The names the task reads. The list lives next to the values it names and is
# read by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "GITHUB_REPO",
    "ARCHIVE_NAME_TEMPLATE",
    "CHECKSUM_FILE_NAME",
    "FALLBACK_PACKAGES",
    "UDEV_RULES_PACKAGE_NAME",
    "APT_BINARY_PATH",
    "THEME_ICON_NAME",
    "DOWNLOAD_DIR",
    "INSTALL_DIR_RELATIVE_PATH",
    "COMMAND_RELATIVE_PATH",
    "LAUNCHER_RELATIVE_PATH",
    "CONSOLE_LAUNCHER_RELATIVE_PATH",
    "LAUNCHER_TEMPLATE_FILE_NAME",
    "CONSOLE_LAUNCHER_TEMPLATE_FILE_NAME",
    "BINARY_FILE_NAME",
    "SERVER_FILE_NAME",
    "ADB_FILE_NAME",
    "ICON_FILE_NAME",
    "EXTRACT_DIR_PREFIX",
    "TRASH_DIR_RELATIVE_PATH",
    "VERSION_COMMAND",
    "CHECKSUM_COMMAND",
    "ARCHIVE_EXTRACT_COMMAND",
)
