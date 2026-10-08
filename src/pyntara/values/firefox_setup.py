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

# Repository of browser defaults, cloned into SETTINGS_DIR, and the branch the
# task follows.
SETTINGS_REPO_URL: str = (
    "https://github.com/Borodin-Atamanov/firefox-default-settings.git"
)
SETTINGS_REPO_REF: str = "main"

# Root cache directory that holds the clone of the defaults repository.
SETTINGS_DIR: Path = Path("/var/cache/pyntara/firefox-settings")

# Root the system/ tree of the defaults repository is deployed under, with the
# relative paths preserved.
SYSTEM_ROOT: Path = Path("/")

# Directory inside the defaults repository whose tree is deployed under
# SYSTEM_ROOT. It carries the machine policy and the AutoConfig entry point with
# the defaults file next to the browser.
SETTINGS_SYSTEM_TREE_RELATIVE_PATH: str = "system"

# Paths of the defaults repository that are never applied to the machine.
# The official Mozilla apt repository: the armored signing key, the deb822
# source that verifies against it, and the apt preferences file that keeps the
# Ubuntu transitional package from winning the install. The key is already
# armored, so it is written as downloaded and no dearmor step is needed.
MOZILLA_KEY_URL: str = "https://packages.mozilla.org/apt/repo-signing-key.gpg"
KEYRING_PATH: Path = Path("/usr/share/keyrings/packages.mozilla.org.asc")
KEYRING_TEMP_DIR_PREFIX: str = "pyntara-firefox-"
APT_SOURCE_PATH: Path = Path("/etc/apt/sources.list.d/mozilla.sources")
APT_PREFERENCES_PATH: Path = Path("/etc/apt/preferences.d/mozilla")

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
APT_INSTALL_COMMAND: tuple[str, ...] = (
    "apt-get",
    "install",
    "--yes",
    "--allow-downgrades",
    "{package}",
)
SNAP_REMOVE_COMMAND: tuple[str, ...] = ("snap", "remove", "{snap}")
PROCESS_CHECK_COMMAND: tuple[str, ...] = ("pgrep", "-x", "{process_name}")

# Commands that clone, update and compare the defaults repository; {url}, {ref}
# and {dir} are the repository, the configured ref and the clone directory.
SETTINGS_CLONE_COMMAND: tuple[str, ...] = (
    "git",
    "clone",
    "--quiet",
    "--depth",
    "1",
    "--branch",
    "{ref}",
    "{url}",
    "{dir}",
)
SETTINGS_FETCH_COMMAND: tuple[str, ...] = (
    "git",
    "-C",
    "{dir}",
    "fetch",
    "--quiet",
    "origin",
    "{ref}",
)
SETTINGS_REVISION_COMMAND: tuple[str, ...] = (
    "git",
    "-C",
    "{dir}",
    "rev-parse",
    "{revision}",
)
SETTINGS_RESET_COMMAND: tuple[str, ...] = (
    "git",
    "-C",
    "{dir}",
    "reset",
    "--hard",
    "{revision}",
)

# The packaged desktop entry of the browser, the id the default-browser setting
# and the panel launcher point at, and the command that makes the browser the
# default of the desktop user.
DESKTOP_FILE_NAME: str = "firefox.desktop"
PANEL_LAUNCHER_ID: str = "applications:firefox.desktop"
DEFAULT_BROWSER_COMMAND: tuple[str, ...] = (
    "xdg-settings",
    "set",
    "default-web-browser",
    "{desktop_id}",
)
DEFAULT_BROWSER_QUERY_COMMAND: tuple[str, ...] = (
    "xdg-settings",
    "get",
    "default-web-browser",
)

# Name of the desktop user appletsrc that carries the pinned taskbar launchers,
# as the KConfig tools take it, and its path under the home of the user.
APPLETSRC_FILE_NAME: str = "plasma-org.kde.plasma.desktop-appletsrc"
APPLETSRC_RELATIVE_PATH: str = ".config/plasma-org.kde.plasma.desktop-appletsrc"

# The task manager applet plugins whose launcher list receives the Firefox
# button: the icons-only task manager and the classic one.
TASKBAR_PLUGIN_NAMES: tuple[str, ...] = (
    "org.kde.plasma.icontasks",
    "org.kde.plasma.taskmanager",
)

# The appletsrc key that carries the pinned launchers and the group below a task
# manager applet that holds them, as the group segments Plasma nests the file
# with. Measured on Kubuntu 26.04 with KDE 6.6 (docs/spec/chrome-setup.md).
APPLETSRC_LAUNCHERS_KEY: str = "launchers"
APPLETSRC_LAUNCHER_GROUP: tuple[str, ...] = ("Configuration", "General")

# Vocabulary of the KConfig tools the task reads and writes the appletsrc with.
KREADCONFIG_COMMAND: tuple[str, ...] = ("kreadconfig6", "--file", "{file_name}")
KWRITECONFIG_COMMAND: tuple[str, ...] = ("kwriteconfig6", "--file", "{file_name}")
CONFIG_GROUP_FLAG: tuple[str, ...] = ("--group", "{group}")
CONFIG_KEY_FLAG: tuple[str, ...] = ("--key", "{key}")

# Prefix that runs a command as the desktop user.
RUNUSER_COMMAND: tuple[str, ...] = ("runuser", "-u", "{username}", "--")

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "SETTINGS_REPO_URL",
    "SETTINGS_REPO_REF",
    "SETTINGS_DIR",
    "SYSTEM_ROOT",
    "SETTINGS_SYSTEM_TREE_RELATIVE_PATH",
    "MOZILLA_KEY_URL",
    "KEYRING_PATH",
    "KEYRING_TEMP_DIR_PREFIX",
    "APT_SOURCE_PATH",
    "APT_PREFERENCES_PATH",
    "APT_SOURCE_TEMPLATE_FILE_NAME",
    "APT_PREFERENCES_TEMPLATE_FILE_NAME",
    "PACKAGE_NAME",
    "PROCESS_NAME",
    "SNAP_NAME",
    "APT_INSTALL_COMMAND",
    "SNAP_REMOVE_COMMAND",
    "PROCESS_CHECK_COMMAND",
    "SETTINGS_CLONE_COMMAND",
    "SETTINGS_FETCH_COMMAND",
    "SETTINGS_REVISION_COMMAND",
    "SETTINGS_RESET_COMMAND",
    "DESKTOP_FILE_NAME",
    "PANEL_LAUNCHER_ID",
    "DEFAULT_BROWSER_COMMAND",
    "DEFAULT_BROWSER_QUERY_COMMAND",
    "APPLETSRC_FILE_NAME",
    "APPLETSRC_RELATIVE_PATH",
    "TASKBAR_PLUGIN_NAMES",
    "APPLETSRC_LAUNCHERS_KEY",
    "APPLETSRC_LAUNCHER_GROUP",
    "KREADCONFIG_COMMAND",
    "KWRITECONFIG_COMMAND",
    "CONFIG_GROUP_FLAG",
    "CONFIG_KEY_FLAG",
    "RUNUSER_COMMAND",
)
