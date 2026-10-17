"""Values of the ventoy_install task.

The values of the Ventoy section as typed constants: the release repository,
the asset name, the versioned install directory with its stable command link,
the download cache and the menu entry that starts the graphical window of
Ventoy (docs/spec/ventoy-install.md).

Ventoy is distributed only as a release archive: it has no package in the
Ubuntu archive and no apt repository of its own, so the release of the
configured GitHub repository is the single source and the version is chased
on every run.
"""

from __future__ import annotations

from pathlib import Path

# Owner and name pair of the release repository.
GITHUB_REPO: str = "ventoy/Ventoy"

# Prefix of the release tag. The tag carries a v in front of the version
# (v1.1.18) while the asset name and the version file of the release do not,
# so the prefix is stripped before the version is used or compared.
RELEASE_TAG_PREFIX: str = "v"

# Asset name of the Linux release with the version substituted, for example
# ventoy-1.1.18-linux.tar.gz. The archive carries the tool trees of every
# architecture, so the name never depends on the machine.
ARCHIVE_NAME_TEMPLATE: str = "ventoy-{version}-linux.tar.gz"

# Root cache of the downloaded archive.
DOWNLOAD_DIR: Path = Path("/var/cache/pyntara/ventoy")

# Directory of one subdirectory per installed release version, and the
# symbolic link of the install directory that names the active version. The
# link is what every reader uses, so a path in the menu entry never changes
# when a new version is installed.
INSTALL_DIR: Path = Path("/opt/ventoy")
CURRENT_LINK_NAME: str = "current"

# The symbolic link of the active version, written out once so a command or a
# message names the same path.
CURRENT_LINK_PATH: Path = INSTALL_DIR / CURRENT_LINK_NAME

# Mode of the install directory and of every version directory inside it.
INSTALL_DIR_MODE: int = 0o755

# Prefix of the temporary directory the archive is unpacked into: the
# unpacking happens inside the install directory, so the finished tree is
# moved into place by one rename on the same filesystem.
EXTRACT_DIR_PREFIX: str = ".unpack-"

# Suffix of the name a version directory takes when it is replaced by a fresh
# unpack of the same version. The old tree is kept and never deleted.
SUPERSEDED_SUFFIX: str = "-superseded"

# Path of the version file inside an installed release tree. The content of
# the file is the version, so a directory renamed by hand is caught.
VERSION_FILE_RELATIVE_PATH: str = "ventoy/version"

# Files every complete release tree carries. The graphical binary is checked
# apart, because its name depends on the machine architecture.
TREE_MARKER_FILES: tuple[str, ...] = (
    "boot/boot.img",
    "ventoy/version",
    "Ventoy2Disk.sh",
)

# Name of the graphical launcher inside a release tree, with the release
# spelling of the machine architecture substituted (VentoyGUI.x86_64).
GUI_BINARY_NAME_TEMPLATE: str = "VentoyGUI.{asset_arch}"

# Icon of the menu entry inside a release tree.
ICON_RELATIVE_PATH: str = "WebUI/static/img/VentoyLogo.png"

# Menu entry of the desktop, and its template under task_data/ventoy_install/.
DESKTOP_ENTRY_PATH: Path = Path("/usr/local/share/applications/ventoy.desktop")
DESKTOP_TEMPLATE_FILE_NAME: str = "ventoy.desktop"

# Mode of the menu entry.
DESKTOP_ENTRY_MODE: int = 0o644

# Unpacking command of the release archive, with the archive and the
# directory as its placeholders.
ARCHIVE_EXTRACT_COMMAND: tuple[str, ...] = (
    "tar",
    "--extract",
    "--gzip",
    "--file",
    "{archive}",
    "--directory",
    "{extract_dir}",
)

# Acceptance query of an unpacked tree: with the help flag the console front
# end prints its own version and its usage without touching any disk and exits
# zero, so an unpacked tree that lost a tool or cannot inflate its packed tools
# is caught before the link is switched. Without the flag the front end prints
# the same usage and exits one (measured on 2026-10-16), which is why the flag
# is part of the query. The script path is the placeholder.
ACCEPTANCE_COMMAND: tuple[str, ...] = ("bash", "{script}", "-h")

# The line the acceptance query must carry, with the version substituted.
ACCEPTANCE_EXPECTED_TEXT: str = "Ventoy: {version}"

# Query of the library cache, and the libraries the graphical window needs:
# the window is a GTK or a Qt program, so at least one of the two must be
# visible to the linker.
LINKER_CACHE_QUERY_COMMAND: tuple[str, ...] = ("ldconfig", "-p")
TOOLKIT_LIBRARY_NAMES: tuple[str, ...] = ("libgtk-3.so", "libQt5Widgets.so")

# The names the task reads. The list lives next to the values it names and is
# read by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "GITHUB_REPO",
    "RELEASE_TAG_PREFIX",
    "ARCHIVE_NAME_TEMPLATE",
    "DOWNLOAD_DIR",
    "INSTALL_DIR",
    "CURRENT_LINK_NAME",
    "CURRENT_LINK_PATH",
    "INSTALL_DIR_MODE",
    "EXTRACT_DIR_PREFIX",
    "SUPERSEDED_SUFFIX",
    "VERSION_FILE_RELATIVE_PATH",
    "TREE_MARKER_FILES",
    "GUI_BINARY_NAME_TEMPLATE",
    "ICON_RELATIVE_PATH",
    "DESKTOP_ENTRY_PATH",
    "DESKTOP_TEMPLATE_FILE_NAME",
    "DESKTOP_ENTRY_MODE",
    "ARCHIVE_EXTRACT_COMMAND",
    "ACCEPTANCE_COMMAND",
    "ACCEPTANCE_EXPECTED_TEXT",
    "LINKER_CACHE_QUERY_COMMAND",
    "TOOLKIT_LIBRARY_NAMES",
)
