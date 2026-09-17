"""Values of the chrome_setup task.

The task installs Google Chrome from the official Google apt repository, applies
the browser settings of the chromium-default-settings repository to the live
profile of the desktop user, and writes a desktop entry override that starts
Chrome with the local proxy when it listens, the profile mirror and the DevTools
listener on the loopback address (docs/spec/chrome-setup.md).

The desktop user and his home and the mode of every deployed file come from the
shared module. The address and the port of the local proxy belong to the
three_x_ui_xray_setup section and are read from there by the task, not from this
module.
"""

from __future__ import annotations

from pathlib import Path

# Repository of browser settings, cloned into SETTINGS_DIR, and the branch the
# task follows.
SETTINGS_REPO_URL: str = (
    "https://github.com/Borodin-Atamanov/chromium-default-settings.git"
)
SETTINGS_REPO_REF: str = "main"

# Root cache directory that holds the clone of the settings repository.
SETTINGS_DIR: Path = Path("/var/cache/pyntara/chromium-settings")

# Root the system/ tree of the settings repository is deployed under, with the
# relative paths preserved.
SYSTEM_ROOT: Path = Path("/")

# The Google apt repository: the deb822 source file and the keyring that
# verifies it, downloaded from GOOGLE_KEY_URL.
APT_SOURCE_PATH: Path = Path("/etc/apt/sources.list.d/google-chrome.sources")
KEYRING_PATH: Path = Path("/usr/share/keyrings/google-chrome.gpg")
GOOGLE_KEY_URL: str = "https://dl.google.com/linux/linux_signing_key.pub"

# The packaged desktop entry and the override that receives the launch flags.
# The override lives in /usr/local/share/applications, ahead of the packaged
# entry in the XDG search order.
DESKTOP_SOURCE_PATH: Path = Path("/usr/share/applications/google-chrome.desktop")
DESKTOP_OVERRIDE_PATH: Path = Path(
    "/usr/local/share/applications/google-chrome.desktop"
)

# Key of a desktop entry line that starts the program. The task appends the
# launch flags to every line carrying it, so the key of the foreign file is a
# value like the flag list itself.
DESKTOP_ENTRY_EXEC_KEY: str = "Exec="

# Name of the apt package of the browser and the name of its main process as
# pgrep sees it (comm is the binary name, not the wrapper).
PACKAGE_NAME: str = "google-chrome-stable"
PROCESS_NAME: str = "chrome"

# Name of the desktop user appletsrc that carries the pinned taskbar launchers,
# as the KConfig tools take it, and its path under the home of the user.
APPLETSRC_FILE_NAME: str = "plasma-org.kde.plasma.desktop-appletsrc"
APPLETSRC_RELATIVE_PATH: str = ".config/plasma-org.kde.plasma.desktop-appletsrc"

# The task manager applet plugins whose launcher list receives the Chrome
# button: the icons-only task manager and the classic one, so a desktop with
# either widget pins the button.
TASKBAR_PLUGIN_NAMES: tuple[str, ...] = (
    "org.kde.plasma.icontasks",
    "org.kde.plasma.taskmanager",
)

# The appletsrc key that carries the pinned launchers of a task manager applet
# and the group below such an applet that holds them, written as the group
# segments Plasma nests the file with. A desktop whose panel keeps its launchers
# elsewhere is answered here.
APPLETSRC_LAUNCHERS_KEY: str = "launchers"
APPLETSRC_LAUNCHER_GROUP: tuple[str, ...] = ("Configuration", "General")

# Vocabulary of the KConfig tools the task reads and writes the appletsrc with:
# the two calls carry the file as {file_name}, a group is selected with
# CONFIG_GROUP_FLAG, a key with CONFIG_KEY_FLAG, and the value of a write is a
# positional argument the code appends.
KREADCONFIG_COMMAND: tuple[str, ...] = ("kreadconfig6", "--file", "{file_name}")
KWRITECONFIG_COMMAND: tuple[str, ...] = ("kwriteconfig6", "--file", "{file_name}")
CONFIG_GROUP_FLAG: tuple[str, ...] = ("--group", "{group}")
CONFIG_KEY_FLAG: tuple[str, ...] = ("--key", "{key}")

# Launcher id pinned to the panel; it resolves to the CDP desktop override in
# the XDG applications directories.
PANEL_LAUNCHER_ID: str = "applications:google-chrome.desktop"

# Command that restarts the Plasma panel of the desktop user, so a newly pinned
# launcher appears without a re-login; {username} is the account of the machine.
PANEL_RESTART_COMMAND: tuple[str, ...] = (
    "systemctl",
    "--user",
    "--machine",
    "{username}@.host",
    "restart",
    "plasma-plasmashell.service",
)

# Directory inside the settings repository whose tree is deployed under
# SYSTEM_ROOT with the relative paths preserved.
SETTINGS_SYSTEM_TREE_RELATIVE_PATH: str = "system"

# Relative path of the browser settings file inside the settings repository and
# inside the live Chrome profile of the desktop user, and the profile directory
# under the home of the user.
PREFERENCES_RELATIVE_PATH: str = "Default/Preferences"
PROFILE_DIR_RELATIVE_PATH: str = ".config/google-chrome"

# Prefix of the temporary directory the Google keyring is downloaded and
# dearmored in, and the name of the armored key file inside it, before the file
# is dearmored into KEYRING_PATH.
KEYRING_TEMP_DIR_PREFIX: str = "pyntara-chrome-"
KEYRING_ARMORED_FILE_NAME: str = "google-chrome-key.pub"

# Name of the deb822 apt source template under task_data/chrome_setup/ of the
# clone, rendered with $keyring_path.
APT_SOURCE_TEMPLATE_FILE_NAME: str = "google-chrome.sources"

# Flags appended to every Exec line of the packaged desktop entry, in order,
# each with the placeholders it needs. A flag whose placeholder has no value is
# left out, so a piece that is not in place costs the browser that one flag and
# not the whole start.
LAUNCH_FLAGS: tuple[str, ...] = (
    "--proxy-server={proxy_server}",
    "--user-data-dir={user_data_dir}",
    "--remote-debugging-port={cdp_port}",
    "--remote-debugging-address={cdp_address}",
)

# Command that dearmors the downloaded Google signing key into the keyring path;
# {armored} and {output} are the downloaded file and the keyring.
KEYRING_DEARMOR_COMMAND: tuple[str, ...] = (
    "gpg",
    "--dearmor",
    "--output",
    "{output}",
    "{armored}",
)

# Commands that clone, update and compare the settings repository; {url}, {ref}
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

# Command that tells whether the browser runs; {process_name} is PROCESS_NAME.
PROCESS_CHECK_COMMAND: tuple[str, ...] = ("pgrep", "-x", "{process_name}")

# Command that reports the mount that contains a path, used to confirm the
# profile mirror; {path} is the path to inspect.
MOUNT_CHECK_COMMAND: tuple[str, ...] = (
    "findmnt",
    "--noheadings",
    "--output",
    "TARGET,FSROOT",
    "--target",
    "{path}",
)

# Commands that install the boot-time mirror unit: reload the unit files and
# enable and start the oneshot unit whose name is substituted.
MOUNT_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
MOUNT_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "--now",
    "{unit_name}",
)

# Command that rebuilds the KDE menu cache for the desktop user, with the HOME
# and XDG_MENU_PREFIX of a Plasma session so the cache looks up
# plasma-applications.menu.
MENU_REFRESH_COMMAND: tuple[str, ...] = (
    "runuser",
    "-u",
    "{username}",
    "--",
    "env",
    "HOME={home_dir}",
    "XDG_MENU_PREFIX=plasma-",
    "kbuildsycoca6",
    "--noincremental",
)

# Path of the profile mirror: a bind mount of the live profile that the task
# creates and the unit below restores at every boot. Branded Google Chrome
# refuses the DevTools listener on the default data directory and asks for a
# non-default one, so Chrome is started with --user-data-dir on this second
# path: the same live profile under another directory name, cookies and logins
# included.
PROFILE_MIRROR_PATH: Path = Path("/home/i/.config/google-chrome-cdp")

# Name of the oneshot systemd unit that bind mounts the profile mirror, so the
# DevTools listener keeps working after a reboot, and of its template under
# task_data/chrome_setup/ of the clone.
MOUNT_SERVICE_UNIT_NAME: str = "mount_chrome_user_dir.service"
MOUNT_UNIT_TEMPLATE_FILE_NAME: str = "mount_chrome_user_dir.service"

# Port and address of the Chrome DevTools listener; the address is the loopback
# one, so the listener is reachable from this machine alone.
CDP_PORT: int = 19222
CDP_ADDRESS: str = "127.0.0.1"

# Prefix that runs a command as the desktop user, so the task reaches the
# session files the user owns; {username} is the account of the machine.
RUNUSER_COMMAND: tuple[str, ...] = ("runuser", "-u", "{username}", "--")

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "SETTINGS_REPO_URL",
    "SETTINGS_REPO_REF",
    "SETTINGS_DIR",
    "SYSTEM_ROOT",
    "APT_SOURCE_PATH",
    "KEYRING_PATH",
    "GOOGLE_KEY_URL",
    "DESKTOP_SOURCE_PATH",
    "DESKTOP_OVERRIDE_PATH",
    "DESKTOP_ENTRY_EXEC_KEY",
    "PACKAGE_NAME",
    "PROCESS_NAME",
    "APPLETSRC_FILE_NAME",
    "APPLETSRC_RELATIVE_PATH",
    "TASKBAR_PLUGIN_NAMES",
    "APPLETSRC_LAUNCHERS_KEY",
    "APPLETSRC_LAUNCHER_GROUP",
    "KREADCONFIG_COMMAND",
    "KWRITECONFIG_COMMAND",
    "CONFIG_GROUP_FLAG",
    "CONFIG_KEY_FLAG",
    "PANEL_LAUNCHER_ID",
    "PANEL_RESTART_COMMAND",
    "SETTINGS_SYSTEM_TREE_RELATIVE_PATH",
    "PREFERENCES_RELATIVE_PATH",
    "PROFILE_DIR_RELATIVE_PATH",
    "KEYRING_TEMP_DIR_PREFIX",
    "KEYRING_ARMORED_FILE_NAME",
    "APT_SOURCE_TEMPLATE_FILE_NAME",
    "LAUNCH_FLAGS",
    "KEYRING_DEARMOR_COMMAND",
    "SETTINGS_CLONE_COMMAND",
    "SETTINGS_FETCH_COMMAND",
    "SETTINGS_REVISION_COMMAND",
    "SETTINGS_RESET_COMMAND",
    "PROCESS_CHECK_COMMAND",
    "MOUNT_CHECK_COMMAND",
    "MOUNT_RELOAD_COMMAND",
    "MOUNT_ENABLE_COMMAND",
    "MENU_REFRESH_COMMAND",
    "PROFILE_MIRROR_PATH",
    "MOUNT_SERVICE_UNIT_NAME",
    "MOUNT_UNIT_TEMPLATE_FILE_NAME",
    "CDP_PORT",
    "CDP_ADDRESS",
    "RUNUSER_COMMAND",
)
