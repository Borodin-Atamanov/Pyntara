"""Values of the firefox_setup task.

The task installs Firefox from the official Mozilla apt repository in place of
the snap, applies the browser defaults of the firefox-default-settings
repository (the machine policy that sets the default search engine and installs
the extensions, and the AutoConfig entry point that sets user-changeable
interface defaults), sets Firefox as the default browser of the desktop user
and pins the Firefox launcher to the Plasma taskbar (docs/spec/firefox-setup.md).

The desktop user and his home and the mode of every deployed file come from the
shared module. The browser is not started through the local proxy: firefox_setup
takes no dependency on three_x_ui_xray_setup.
"""

from __future__ import annotations

from pathlib import Path

# Repository of browser defaults, cloned into SETTINGS_DIR; the branch it is
# brought to is the shared SETTINGS_REPO_REF.
SETTINGS_REPO_URL: str = (
    "https://github.com/Borodin-Atamanov/firefox-default-settings.git"
)

# Root cache directory that holds the clone of the defaults repository.
SETTINGS_DIR: Path = Path("/var/cache/pyntara/firefox-settings")

# The shared SETTINGS_SYSTEM_TREE_RELATIVE_PATH names the tree of the defaults
# repository that is deployed under the shared SYSTEM_ROOT. It carries the
# machine policy and the AutoConfig entry point with the defaults file next to
# the browser. The paths below are never applied to the machine.
# The official Mozilla apt repository: the armored signing key, the deb822
# source that verifies against it, and the apt preferences file that keeps the
# Ubuntu transitional package from winning the install. The key is already
# armored, so it is written as downloaded and no dearmor step is needed.
MOZILLA_KEY_URL: str = "https://packages.mozilla.org/apt/repo-signing-key.gpg"
KEYRING_PATH: Path = Path("/usr/share/keyrings/packages.mozilla.org.asc")
KEYRING_TEMP_DIR_PREFIX: str = "pyntara-firefox-"
KEYRING_ARMORED_FILE_NAME: str = "packages.mozilla.org.asc"
APT_SOURCE_PATH: Path = Path("/etc/apt/sources.list.d/mozilla.sources")
APT_PREFERENCES_PATH: Path = Path("/etc/apt/preferences.d/mozilla")

# Legacy single-line source of the same Mozilla repository, written by an
# earlier scheme that kept the signing key in /etc/apt/keyrings. While it stands
# beside APT_SOURCE_PATH it describes the repository with a different signing
# key, and apt refuses to read the whole source list with "Conflicting values
# set for option Signed-By regarding source ... mozilla", so no package
# operation works on the machine. The task moves the file next to itself under
# LEGACY_SOURCE_BACKUP_SUFFIX, a name apt ignores, so nothing is deleted and the
# move is reversible by hand.
LEGACY_SOURCE_PATH: Path = Path("/etc/apt/sources.list.d/mozilla.list")
LEGACY_SOURCE_BACKUP_SUFFIX: str = ".bak"

# Names of the apt source template and the apt preferences template under
# task_data/firefox_setup/ of the clone; the source is rendered with
# $keyring_path.
APT_SOURCE_TEMPLATE_FILE_NAME: str = "mozilla.sources"
APT_PREFERENCES_TEMPLATE_FILE_NAME: str = "mozilla.pref"

# Name of the apt package of the browser, of its main process as pgrep sees it
# and of the snap it replaces. The Mozilla package carries no epoch while the
# Ubuntu transitional package carries the epoch 1, so the install needs
# --allow-downgrades to replace it.
PACKAGE_NAME: str = "firefox"
PROCESS_NAME: str = "firefox"
SNAP_NAME: str = "firefox"

# The binary the real Mozilla package installs. The Ubuntu archive ships only the
# transitional package firefox (1:1snap1), whose presence in dpkg says nothing
# about a working browser: it installs the snap and provides /usr/bin/firefox
# alone. The task treats the browser as installed only when this file exists, so
# a machine that carries the transitional package alone still gets the Mozilla
# build (measured on liveusb_test on 2026-10-08).
BROWSER_BINARY_PATH: Path = Path("/usr/lib/firefox/firefox")

# The flags of the browser install that the shared install factory of utils.py
# does not add itself. The wait for the package lock lives in that factory, so
# it is not repeated here.
APT_INSTALL_EXTRA_FLAGS: tuple[str, ...] = ("--allow-downgrades",)
SNAP_REMOVE_COMMAND: tuple[str, ...] = ("snap", "remove", "--terminate", "{snap}")
SNAP_LIST_COMMAND: tuple[str, ...] = ("snap", "list", "{snap}")

# The packaged desktop entry of the browser, the id the default-browser setting
# and the panel launcher point at, and the command that makes the browser the
# default of the desktop user.
DESKTOP_FILE_NAME: str = "firefox.desktop"
PANEL_LAUNCHER_ID: str = "applications:firefox.desktop"

# The default-browser setting: the mimeapps.list of the desktop user, the group
# that carries the default applications and the keys that name the browser. The
# entries are written with kwriteconfig6, because xdg-settings on Kubuntu 26.04
# takes a KDE branch that calls qtpaths, which is not installed (only qtpaths6
# is), and fails (measured on liveusb_test on 2026-10-08).
MIMEAPPS_FILE_NAME: str = "mimeapps.list"
DEFAULT_BROWSER_GROUP: tuple[str, ...] = ("Default Applications",)
DEFAULT_BROWSER_MIME_KEYS: tuple[str, ...] = (
    "x-scheme-handler/http",
    "x-scheme-handler/https",
    "text/html",
)

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "SETTINGS_REPO_URL",
    "SETTINGS_DIR",
    "MOZILLA_KEY_URL",
    "KEYRING_PATH",
    "KEYRING_TEMP_DIR_PREFIX",
    "KEYRING_ARMORED_FILE_NAME",
    "APT_SOURCE_PATH",
    "APT_PREFERENCES_PATH",
    "LEGACY_SOURCE_PATH",
    "LEGACY_SOURCE_BACKUP_SUFFIX",
    "APT_SOURCE_TEMPLATE_FILE_NAME",
    "APT_PREFERENCES_TEMPLATE_FILE_NAME",
    "PACKAGE_NAME",
    "PROCESS_NAME",
    "SNAP_NAME",
    "BROWSER_BINARY_PATH",
    "APT_INSTALL_EXTRA_FLAGS",
    "SNAP_REMOVE_COMMAND",
    "SNAP_LIST_COMMAND",
    "DESKTOP_FILE_NAME",
    "PANEL_LAUNCHER_ID",
    "MIMEAPPS_FILE_NAME",
    "DEFAULT_BROWSER_GROUP",
    "DEFAULT_BROWSER_MIME_KEYS",
)
