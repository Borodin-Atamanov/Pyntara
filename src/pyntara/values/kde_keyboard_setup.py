"""Values of the kde_keyboard_setup task.

The section describes the keyboard layout configuration of the desktop: which
layouts kxkbrc carries, how the layout indicator displays the current layout, and
which calls apply the change immediately (docs/spec/kde-keyboard-setup.md).

The desktop user, his home, the name of the KDE shortcuts file and the boolean
spelling of the KConfig files come from the shared module, because other
desktop sections read them as well.
"""

from __future__ import annotations

from pathlib import Path

from pyntara.values import common as common_values

# Packages that must be present: the provider of kwriteconfig6 and kreadconfig6
# (libkf6config-bin), the DBus client used for the reload (qdbus-qt6) and the
# python3-dbus bindings used to apply layout hotkeys through the kglobalaccel
# daemon. The Qt bindings (python3-pyqt6) turn a portable shortcut into the
# combined Qt key code the daemon takes, so the shared client needs them.
PACKAGES: tuple[str, ...] = (
    "libkf6config-bin",
    "qdbus-qt6",
    "python3-dbus",
    "python3-pyqt6",
)

# The directory of the KDE configuration the task edits. It follows the home of
# the desktop user of the shared module, so that home is written once.
CONFIG_DIR: Path = Path(common_values.DESKTOP_HOME_DIR) / ".config"

# The KConfig file names under CONFIG_DIR that the task manages.
KXKBRC_FILE_NAME: str = "kxkbrc"
APPLETSRC_FILE_NAME: str = "plasma-org.kde.plasma.desktop-appletsrc"

# The Plasma applet whose display style is the layout indicator.
APPLET_PLUGIN: str = "org.kde.plasma.keyboardlayout"

# The keyboard layouts in the order Caps Lock and Shift+Caps Lock cycle through:
# the first is English, the second Russian.
LAYOUTS: tuple[str, ...] = ("us", "ru", "es")

# The XKB switch option: Caps Lock to the first layout, Shift+Caps Lock to the
# second layout.
SWITCH_OPTION: str = "grp:caps_select"

# Whether kwin resets the previous XKB options before applying the configured
# one. This and SWITCH_MODE complete the kxkbrc [Layout] group the way KDE writes
# it; kwin applies the switch option at session start only when the group is
# complete, so a minimal group leaves a freshly installed session on the default
# single layout.
RESET_OLD_OPTIONS: int = 1

# The KDE switch mode written into kxkbrc, part of the complete [Layout] group.
# WinClass is the value KDE writes itself.
SWITCH_MODE: str = "WinClass"

# Whether layout switching is enabled at all.
USE_LAYOUT_SWITCHING: int = 1

# How the keyboard layout indicator shows the current layout; Flag shows the
# country flag instead of the layout name.
INDICATOR_DISPLAY_STYLE: str = "Flag"

# Command that makes kwin re-read the keyboard layout configuration, so the
# layouts apply immediately.
KWIN_RELOAD_COMMAND: tuple[str, ...] = (
    "qdbus6",
    "org.kde.KWin",
    "/KWin",
    "org.kde.KWin.reconfigure",
)

# Command that restarts the Plasma panel so the indicator re-reads its
# configuration.
PANEL_RESTART_COMMAND: tuple[str, ...] = (
    "systemctl",
    "--user",
    "--machine",
    "i@.host",
    "restart",
    "plasma-plasmashell.service",
)

# Per-layout hotkeys are managed by the kde_settings task through its kconfig
# records, not here. Empty by default: no hotkeys.
LAYOUT_SWITCH_SHORTCUTS: dict[str, str] = {}

# The group of kxkbrc that carries the layout settings and the group of the
# appletsrc applet that carries its configuration.
KXKBRC_GROUP: tuple[str, ...] = ("Layout",)
APPLET_CONFIGURATION_GROUP: tuple[str, ...] = ("Configuration", "General")

# The keys the task writes in kxkbrc, in the order KDE writes them.
KXKBRC_KEY_LAYOUT_LIST: str = "LayoutList"
KXKBRC_KEY_DISPLAY_NAMES: str = "DisplayNames"
KXKBRC_KEY_VARIANT_LIST: str = "VariantList"
KXKBRC_KEY_OPTIONS: str = "Options"
KXKBRC_KEY_RESET_OLD_OPTIONS: str = "ResetOldOptions"
KXKBRC_KEY_SWITCH_MODE: str = "SwitchMode"
KXKBRC_KEY_USE: str = "Use"

# The appletsrc key that carries the indicator display style.
DISPLAY_STYLE_KEY: str = "displayStyle"

# The component that owns the layout switcher actions in the shortcut system:
# the unique name the daemon knows and the friendly name it shows.
LAYOUT_SWITCHER_COMPONENT_UNIQUE: str = "KDE Keyboard Layout Switcher"
LAYOUT_SWITCHER_COMPONENT_FRIENDLY: str = "Keyboard Layout Switcher"

# The Qt keyboard modifier flags, which the daemon stores combined with the key
# code of a shortcut. The keys are the modifier names a portable shortcut may
# carry; the values are the flags of Qt itself, so a shortcut the task applies
# live can be composed without a Qt library.
SHORTCUT_MODIFIER_BITS: dict[str, int] = {
    "Ctrl": 0x04000000,
    "Alt": 0x08000000,
    "Shift": 0x02000000,
    "Meta": 0x10000000,
}

# The python script that applies the supported shortcuts through the running
# daemon; it ships under task_data/ of the clone and is passed to the interpreter
# with the hotkey payload as its argument.
APPLY_HOTKEYS_SCRIPT_FILE_NAME: str = "apply_hotkeys.py"

# Prefix that runs a command as the desktop user, so the task reaches the
# session files the user owns; {username} is the account of the machine.
RUNUSER_COMMAND: tuple[str, ...] = ("runuser", "-u", "{username}", "--")

# Vocabulary of the KConfig tools the task reads and writes the keyboard
# configuration with: the two base calls carry the file as {file_name}, a group
# is selected with CONFIG_GROUP_FLAG and a key with CONFIG_KEY_FLAG.
KREADCONFIG_COMMAND: tuple[str, ...] = ("kreadconfig6", "--file", "{file_name}")
KWRITECONFIG_COMMAND: tuple[str, ...] = ("kwriteconfig6", "--file", "{file_name}")
CONFIG_GROUP_FLAG: tuple[str, ...] = ("--group", "{group}")
CONFIG_KEY_FLAG: tuple[str, ...] = ("--key", "{key}")
CONFIG_BOOL_TYPE_FLAG: tuple[str, ...] = ("--type", "bool")

# Command that creates the directories of the user configuration; {path} is
# filled in at the call site.
MKDIR_COMMAND: tuple[str, ...] = ("mkdir", "-p", "{path}")

# Prefix of the call that runs the apply_hotkeys client with the system
# interpreter of the [engine] table; the client source and its payload follow as
# the next arguments of the command line.
PYTHON_SCRIPT_COMMAND: tuple[str, ...] = ("{python}", "-c")

# The names the task reads. The list lives next to the values it names and is
# read by the guard of the task before its first step.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "CONFIG_DIR",
    "KXKBRC_FILE_NAME",
    "APPLETSRC_FILE_NAME",
    "APPLET_PLUGIN",
    "LAYOUTS",
    "SWITCH_OPTION",
    "RESET_OLD_OPTIONS",
    "SWITCH_MODE",
    "USE_LAYOUT_SWITCHING",
    "INDICATOR_DISPLAY_STYLE",
    "KWIN_RELOAD_COMMAND",
    "PANEL_RESTART_COMMAND",
    "LAYOUT_SWITCH_SHORTCUTS",
    "KXKBRC_GROUP",
    "APPLET_CONFIGURATION_GROUP",
    "KXKBRC_KEY_LAYOUT_LIST",
    "KXKBRC_KEY_DISPLAY_NAMES",
    "KXKBRC_KEY_VARIANT_LIST",
    "KXKBRC_KEY_OPTIONS",
    "KXKBRC_KEY_RESET_OLD_OPTIONS",
    "KXKBRC_KEY_SWITCH_MODE",
    "KXKBRC_KEY_USE",
    "DISPLAY_STYLE_KEY",
    "LAYOUT_SWITCHER_COMPONENT_UNIQUE",
    "LAYOUT_SWITCHER_COMPONENT_FRIENDLY",
    "SHORTCUT_MODIFIER_BITS",
    "APPLY_HOTKEYS_SCRIPT_FILE_NAME",
    "RUNUSER_COMMAND",
    "KREADCONFIG_COMMAND",
    "KWRITECONFIG_COMMAND",
    "CONFIG_GROUP_FLAG",
    "CONFIG_KEY_FLAG",
    "CONFIG_BOOL_TYPE_FLAG",
    "MKDIR_COMMAND",
    "PYTHON_SCRIPT_COMMAND",
)
