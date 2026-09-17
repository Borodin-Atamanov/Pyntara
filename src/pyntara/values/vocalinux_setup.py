"""Values of the vocalinux_setup task.

The task installs the official Vocalinux AppImage for the desktop user, the
Wayland injection tools the app needs, the app config, the autostart entry, the
empty KDE action that consumes Meta+S, the input group membership and the
ydotool user unit (docs/spec/vocalinux-setup.md).

The desktop user and his home, the two deployed file modes, the name of the
KConfig shortcut file and the package install pair come from the shared module,
because other sections carry the same values.
"""

from __future__ import annotations

from pathlib import Path

# Root cache that keeps the AppImage of the pinned version, next to the home of
# the desktop user.
DOWNLOAD_DIR: Path = Path("/var/cache/pyntara/vocalinux")

# Pinned Vocalinux release, without the leading v of the release tag. The task
# installs exactly this release, and an upgrade is a change of this value, so the
# app config template and the verified release stay in lockstep.
VERSION: str = "0.16.2"

# Owner and name pair of the Vocalinux repository the release comes from; the
# download url of the asset is composed from it and the engine template
# github_release_download_url.
GITHUB_REPO: str = "VocaHQ/vocalinux"

# Name of the release asset of the pinned version; {version} is the value above
# and {asset_arch} the release architecture the engine mapping
# release_asset_architectures names for this machine.
ASSET_NAME_TEMPLATE: str = "Vocalinux-{version}-{asset_arch}.AppImage"

# System tools the app needs on the Wayland desktop plus the provider of
# kwriteconfig6 that registers the Meta+S consuming shortcut.
PACKAGES: tuple[str, ...] = (
    "wtype",
    "ydotool",
    "wl-clipboard",
    "libkf6config-bin",
)

# Group that owns /dev/input and /dev/uinput on Kubuntu. Without the membership
# the app-level hotkey listener cannot read the keyboard devices.
INPUT_GROUP: str = "input"

# Relative paths under the home of the desktop user: the directory of the
# installed AppImage, the app config, the autostart entry and the empty action
# desktop file that consumes Meta+S.
APPIMAGE_DIR_RELATIVE_PATH: str = ".local/share/vocalinux/appimage"
APP_CONFIG_RELATIVE_PATH: str = ".config/vocalinux/config.json"
AUTOSTART_RELATIVE_PATH: str = ".config/autostart/vocalinux.desktop"
ECHO_DESKTOP_RELATIVE_PATH: str = ".local/share/applications/net.local.echo.desktop"

# Templates under task_data/vocalinux_setup/ of the clone: the app config, the
# autostart entry (rendered with $appimage) and the empty action desktop file.
APP_CONFIG_TEMPLATE_FILE_NAME: str = "config.json"
AUTOSTART_TEMPLATE_FILE_NAME: str = "vocalinux.desktop"
ECHO_DESKTOP_TEMPLATE_FILE_NAME: str = "net.local.echo.desktop"

# The KConfig record of the empty Meta+S consuming shortcut, exactly as the KDE
# System Settings stores a .desktop launch shortcut: the group, the entry inside
# it, the action and the key sequence the action holds. Plasma then consumes
# Meta+S and the S never reaches the focused field, while the app-level listener
# still sees the raw key.
SHORTCUT_GROUP_NAME: str = "services"
SHORTCUT_ENTRY_NAME: str = "net.local.echo.desktop"
SHORTCUT_ACTION_NAME: str = "_launch"
SHORTCUT_KEY_SEQUENCE: str = "Meta+S"

# ydotool user unit enabled for the desktop user.
SERVICE_UNIT_NAME: str = "ydotool.service"

# State the user manager prints for a running unit. The task treats the unit as
# already applied only when the answer is this word.
SERVICE_ACTIVE_STATE: str = "active"

# Prefix that runs a command as the desktop user, so the task reaches the session
# files the user owns; {username} is filled from the shared module.
RUNUSER_COMMAND: tuple[str, ...] = ("runuser", "-u", "{username}", "--")

# Vocabulary of the KConfig tools the task reads and writes the shortcut file
# with: the two base calls carry the file as {file_name}, a group is selected
# with CONFIG_GROUP_FLAG and a key with CONFIG_KEY_FLAG.
KREADCONFIG_COMMAND: tuple[str, ...] = ("kreadconfig6", "--file", "{file_name}")
KWRITECONFIG_COMMAND: tuple[str, ...] = ("kwriteconfig6", "--file", "{file_name}")
CONFIG_GROUP_FLAG: tuple[str, ...] = ("--group", "{group}")
CONFIG_KEY_FLAG: tuple[str, ...] = ("--key", "{key}")

# Commands of the file operations the task runs for the desktop user: {path},
# {owner} and {file_mode} are filled in at the call site.
MKDIR_COMMAND: tuple[str, ...] = ("mkdir", "-p", "{path}")
CHOWN_COMMAND: tuple[str, ...] = ("chown", "{owner}", "{path}")
CHMOD_COMMAND: tuple[str, ...] = ("chmod", "{file_mode}", "{path}")

# Commands of the input group membership; {username} and {input_group} are
# filled in at the call site.
GROUP_MEMBERS_COMMAND: tuple[str, ...] = ("id", "-nG", "{username}")
GROUP_ADD_COMMAND: tuple[str, ...] = ("usermod", "-aG", "{input_group}", "{username}")

# Commands of the user manager of the desktop session; {username} and
# {service_unit_name} are filled in at the call site.
SERVICE_ACTIVE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "--user",
    "--machine",
    "{username}@.host",
    "is-active",
    "{service_unit_name}",
)
SERVICE_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "--user",
    "--machine",
    "{username}@.host",
    "enable",
    "--now",
    "{service_unit_name}",
)

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "DOWNLOAD_DIR",
    "VERSION",
    "GITHUB_REPO",
    "ASSET_NAME_TEMPLATE",
    "PACKAGES",
    "INPUT_GROUP",
    "APPIMAGE_DIR_RELATIVE_PATH",
    "APP_CONFIG_RELATIVE_PATH",
    "AUTOSTART_RELATIVE_PATH",
    "ECHO_DESKTOP_RELATIVE_PATH",
    "APP_CONFIG_TEMPLATE_FILE_NAME",
    "AUTOSTART_TEMPLATE_FILE_NAME",
    "ECHO_DESKTOP_TEMPLATE_FILE_NAME",
    "SHORTCUT_GROUP_NAME",
    "SHORTCUT_ENTRY_NAME",
    "SHORTCUT_ACTION_NAME",
    "SHORTCUT_KEY_SEQUENCE",
    "SERVICE_UNIT_NAME",
    "SERVICE_ACTIVE_STATE",
    "RUNUSER_COMMAND",
    "KREADCONFIG_COMMAND",
    "KWRITECONFIG_COMMAND",
    "CONFIG_GROUP_FLAG",
    "CONFIG_KEY_FLAG",
    "MKDIR_COMMAND",
    "CHOWN_COMMAND",
    "CHMOD_COMMAND",
    "GROUP_MEMBERS_COMMAND",
    "GROUP_ADD_COMMAND",
    "SERVICE_ACTIVE_COMMAND",
    "SERVICE_ENABLE_COMMAND",
)
