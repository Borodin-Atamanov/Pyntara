"""Values of the kde_settings task.

The task configures the desktop of the machine: the dark color scheme and the
dark global theme, the native day and night switch, the cursor theme, the NumLock
state, the touchpad, the Wayland virtual keyboard, the SDDM login screen, the
Places panel, the KWin scripts with their hotkeys, the hidden Places entries, the
virtual desktops and a long list of KConfig values of the desktop applications
(docs/spec/kde-settings.md).

The desktop user and his home, the name of the KConfig shortcut file and the
boolean spelling of the KConfig files come from the shared module.

The KConfig values are data of the section and live in KCONFIG_RECORDS: a tuple of
KconfigRecord entries, each naming a file, a group, a key and the string form of
its value, with the type when the key is a flag and the delete flag when the key
is removed instead of written.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyntara.values import common as common_values


@dataclass(frozen=True)
class KconfigRecord:
    """One KConfig value of the desktop: file, group, key and value.

    The group is the list of group segments Plasma nests a file with, the value
    is the string form the file carries (a KDE flag is the word true or false,
    not 1 or 0), type is the word "bool" for a flag and empty otherwise, and
    delete marks the few records that remove a key instead of writing it, in
    which case value is empty.
    """

    file: str
    group: tuple[str, ...]
    key: str
    value: str = ""
    type: str = ""
    delete: bool = False


# The placeholders a record may carry where a path belongs to the desktop account:
# the task fills them with the account of the machine when it reads or writes the
# record. A record is built when this module is imported, and the engine resolves
# the desktop account later, after the import, so a literal account in a record
# would name whichever machine wrote the values instead of the target machine.
USERNAME_PLACEHOLDER_NAME: str = "username"
HOME_PLACEHOLDER_NAME: str = "home_dir"


# Packages that must be present: the provider of the plasma-apply theme tools,
# the KConfig reader and writer, the Kubuntu settings that ship the light and
# dark global themes, the dbus python client, and the Qt bindings that turn a
# portable keyboard combination into the combined Qt key code the daemon takes.
PACKAGES: tuple[str, ...] = (
    "plasma-workspace",
    "libkf6config-bin",
    "kubuntu-settings-desktop",
    "python3-dbus",
    "python3-pyqt6",
)

# The dark color scheme applied to all Qt and KDE windows, and the light and dark
# global themes: the dark one is the machine theme, the light one is what the
# native day and night switch alternates to during the day. The task copies the
# light theme with its cursor theme into the user look and feel directory.
COLOR_SCHEME: str = "BreezeDark"
LOOK_AND_FEEL: str = "org.kubuntudark.desktop"
LOOK_AND_FEEL_LIGHT: str = "org.kubuntulight.desktop"

# When 1, the native KDE day and night theme switch is turned on and the theme
# itself is left to the switch, so a run never overwrites the current theme; the
# dark theme values above then describe the night side. When 0, the task applies
# the dark theme directly.
AUTOMATIC_LOOK_AND_FEEL: int = 1

# The mouse cursor theme of the session, applied after the KConfig records
# because the day and night switch overwrites cursorTheme when it applies a look
# and feel, and the blue cursor theme written into the light theme defaults.
CURSOR_THEME: str = "Oxygen_Yellow"
CURSOR_THEME_LIGHT: str = "Oxygen_Blue"

# The NumLock state when Plasma starts: on, off or unchanged, stored in
# kcminputrc [Keyboard] NumLock as 0, 1 or 2.
NUMLOCK_ON_BOOT: str = "off"

# Touchpad click method applied to every touchpad of the machine: clickfinger,
# clickareas or none.
TOUCHPAD_CLICK_METHOD: str = "clickareas"

# The Wayland virtual keyboard: whether it is enabled, the input method that
# provides it and the enabled locales written to plasmakeyboardrc.
VIRTUAL_KEYBOARD_ENABLED: int = 1
VIRTUAL_KEYBOARD_INPUT_METHOD: str = (
    "/usr/share/applications/org.kde.plasma.keyboard.desktop"
)
VIRTUAL_KEYBOARD_LOCALES: tuple[str, ...] = ("en_US", "es_MX", "ru_RU")

# Command that makes kwin re-read its configuration, so a changed Wayland input
# method applies without a session restart.
KWIN_RELOAD_COMMAND: tuple[str, ...] = (
    "qdbus6",
    "org.kde.KWin",
    "/KWin",
    "org.kde.KWin.reconfigure",
)

# Prefix of the calls that run a rendered python client with the system
# interpreter of the engine and the path of the rendered client as the next
# argument of the call.
PYTHON_SCRIPT_COMMAND: tuple[str, ...] = ("{python}", "{client_file}")

# The DBus interface of the running KWin session, by its parts: the bus name, the
# object path and the interface name of the virtual desktop manager, and the
# property that lists the desktops, the property that carries the live number of
# desktops and the standard interface that carries the property call. The number
# is a property and not a call: asking for a method of that name is refused by
# the interface.
KWIN_BUS_NAME: str = "org.kde.KWin"
VIRTUAL_DESKTOP_MANAGER_OBJECT_PATH: str = "/VirtualDesktopManager"
VIRTUAL_DESKTOP_MANAGER_INTERFACE_NAME: str = "org.kde.KWin.VirtualDesktopManager"
VIRTUAL_DESKTOPS_PROPERTY_NAME: str = "desktops"
VIRTUAL_DESKTOP_COUNT_PROPERTY_NAME: str = "count"
DBUS_PROPERTIES_INTERFACE_NAME: str = "org.freedesktop.DBus.Properties"

# Commands that read and change the number of virtual desktops through the kwin
# DBus interface: the live count, the creation of one desktop at a position and
# the removal of one desktop by id. The source of truth stays kwinrc; these calls
# only make the running session match it, and the name the create call carries is
# left empty because kwin names a new desktop after its position.
KWIN_DESKTOP_COUNT_COMMAND: tuple[str, ...] = (
    "qdbus6",
    "{kwin_bus_name}",
    "{virtual_desktop_manager_object_path}",
    "{dbus_properties_interface_name}.Get",
    "{virtual_desktop_manager_interface_name}",
    "{virtual_desktop_count_property_name}",
)
KWIN_DESKTOP_CREATE_COMMAND: tuple[str, ...] = (
    "qdbus6",
    "{kwin_bus_name}",
    "{virtual_desktop_manager_object_path}",
    "{virtual_desktop_manager_interface_name}.createDesktop",
    "{position}",
    "{desktop_name}",
)
KWIN_DESKTOP_REMOVE_COMMAND: tuple[str, ...] = (
    "qdbus6",
    "{kwin_bus_name}",
    "{virtual_desktop_manager_object_path}",
    "{virtual_desktop_manager_interface_name}.removeDesktop",
    "{desktop_id}",
)

# The SDDM login screen: autologin into the configured user session and the login
# theme, in the two system files the task writes as root.
SDDM_CONF_FILE: Path = Path("/etc/sddm.conf")
SDDM_THEME_CONF_FILE: Path = Path("/etc/sddm.conf.d/20-kubuntu.conf")

# Paths under the home of the target user: the KDE config directory, the user
# copy of the KWin scripts, the user copy of the global themes, the Places panel
# file, the XDG user directories file and the Konsole profile. A relative path
# starts at the home of the desktop user.
USER_CONFIG_DIR: str = ".config"
USER_KWIN_SCRIPTS_DIR: str = ".local/share/kwin/scripts"
USER_LOOK_AND_FEEL_DIR: str = ".local/share/plasma/look-and-feel"
USER_PLACES_FILE: str = ".local/share/user-places.xbel"
USER_DIRS_FILE: str = "user-dirs.dirs"
KONSOLE_PROFILE_PATH: str = ".local/share/konsole/Pyntara.profile"

# Modes of the user files the task writes: the KWin script files are readable by
# the desktop session, the files that carry this machine's own settings stay
# private to the user. Neither is the shared pair of the deployed desktop files,
# which carries 0644 and 0755, because a private file is a different meaning.
SCRIPT_FILE_MODE: int = 0o644
DEFAULT_FILE_MODE: int = 0o600

# The system copy of the global themes, the directory inside a theme that carries
# its defaults, and the SDDM autologin and theme settings.
SYSTEM_LOOK_AND_FEEL_DIR: Path = Path("/usr/share/plasma/look-and-feel")
THEME_DEFAULTS_DIR: str = "contents/defaults"
SDDM_AUTOLOGIN_USER: str = common_values.DESKTOP_USERNAME
SDDM_AUTOLOGIN_SESSION: str = "plasma"
SDDM_THEME: str = "kubuntu"
SDDM_THEME_CURSOR_SIZE: str = "30"
SDDM_THEME_CURSOR_THEME: str = "breeze_cursors"
SDDM_THEME_FONT: str = "Noto Sans,20"

# The Dolphin Places panel entries hidden on the target machine, matched by their
# bookmark title. The hidden set matches the recorded setup where every XDG user
# directory is folded into Downloads, so only Downloads stays visible among the
# default places; an empty list leaves the panel untouched.
PLACES_HIDDEN: tuple[str, ...] = (
    "Home",
    "Desktop",
    "Documents",
    "Music",
    "Pictures",
    "Videos",
)

# The XBEL namespaces of user-places.xbel, by the prefix the file binds them to.
# Dolphin can write the file with a namespace bound as ns0 while the bookmark
# prefix stays undeclared, which a strict parser rejects, so the task declares
# these prefixes.
PLACES_NAMESPACES: dict[str, str] = {
    "bookmark": "http://freedesktop.org/standards/desktop-bookmarks",
    "kdepriv": "http://www.kde.org/kdepriv",
    "mime": "http://freedesktop.org/standards/shared-mime-info",
}

# Owner address of the KDE metadata block inside an entry of that file: only a
# block with this owner is a place the task may hide.
PLACES_METADATA_OWNER: str = "http://www.kde.org"

# The KConfig files the task writes, by the name they carry under the user config
# directory, and the group names inside them. They are the vocabulary of foreign
# files, so a KDE release that renames one of them is answered here.
KDEGLOBALS_FILE_NAME: str = "kdeglobals"
KCMINPUTRC_FILE_NAME: str = "kcminputrc"
KWINRC_FILE_NAME: str = "kwinrc"
PLASMA_KEYBOARD_FILE_NAME: str = "plasmakeyboardrc"
GENERAL_GROUP: tuple[str, ...] = ("General",)
KDE_GROUP: tuple[str, ...] = ("KDE",)
MOUSE_GROUP: tuple[str, ...] = ("Mouse",)
KEYBOARD_GROUP: tuple[str, ...] = ("Keyboard",)
WAYLAND_GROUP: tuple[str, ...] = ("Wayland",)
VIRTUAL_KEYBOARD_GROUP: tuple[str, ...] = ("General",)
PLUGINS_GROUP: tuple[str, ...] = ("Plugins",)
DESKTOPS_GROUP: tuple[str, ...] = ("Desktops",)

# The vocabulary of a touchpad group in kcminputrc, which names one device: the
# root group of a libinput device and the word a device name ends with, by which
# the task finds every touchpad of the machine.
TOUCHPAD_GROUP_ROOT: str = "Libinput"
TOUCHPAD_DEVICE_WORD: str = "Touchpad"

# The keys the task reads and writes in those files.
LOOK_AND_FEEL_PACKAGE_KEY: str = "LookAndFeelPackage"
COLOR_SCHEME_KEY: str = "ColorScheme"
AUTOMATIC_LOOK_AND_FEEL_KEY: str = "AutomaticLookAndFeel"
AUTOMATIC_LOOK_AND_FEEL_IDLE_INTERVAL_KEY: str = "AutomaticLookAndFeelIdleInterval"
NUMLOCK_KEY: str = "NumLock"
INPUT_METHOD_KEY: str = "InputMethod"
INPUT_METHOD_LOCALES_KEY: str = "enabledLocales"
CURSOR_THEME_KEY: str = "cursorTheme"
CLICK_METHOD_KEY: str = "ClickMethod"
DESKTOP_COUNT_KEY: str = "Number"

# The NumLock and click method values as those files store them.
NUMLOCK_VALUES: dict[str, str] = {"on": "0", "off": "1", "unchanged": "2"}
CLICK_METHOD_VALUES: dict[str, str] = {
    "clickfinger": "1",
    "clickareas": "2",
    "none": "0",
}

# The idle wait, in minutes, before the native day and night theme switch applies
# its new theme; recorded from the user's manual tuning.
AUTOMATIC_THEME_SWITCH_IDLE_INTERVAL: str = "99"

# The structure of user-places.xbel the task matches on: the root tag, the entry
# tag, the title tag inside an entry, the metadata path, the attribute that
# carries the owner and the element and value that mark a hidden place.
PLACES_ROOT_TAG: str = "xbel"
PLACES_BOOKMARK_TAG: str = "bookmark"
PLACES_TITLE_TAG: str = "title"
PLACES_METADATA_PATH: str = "info/metadata"
PLACES_METADATA_OWNER_ATTRIBUTE: str = "owner"
PLACES_HIDDEN_ELEMENT: str = "IsHidden"
PLACES_HIDDEN_VALUE: str = "true"

# The KWin scripts the task installs and enables, the files each script carries
# inside its directory, the keyboard combinations the scripts claim and the
# actions that own those combinations. A combination is listed in the portable
# form KWin writes; the shared client turns it into the combined Qt key code the
# daemon takes, so no table of hand written codes is needed.
KWIN_SCRIPTS: tuple[str, ...] = ("window-grow-shrink", "window-restore-tracker")
KWIN_SCRIPT_FILES: tuple[str, ...] = ("metadata.json", "contents/code/main.js")
KWIN_SCRIPT_HOTKEYS: tuple[str, ...] = ("Meta+Ctrl+Up", "Meta+Ctrl+Down")
KWIN_SCRIPT_ACTIONS: tuple[str, ...] = (
    "Grow Window by 5px",
    "Shrink Window by 5px",
)

# The word the first field of a shortcut record carries when its action owns no
# combination: the task reads it as "no combination".
SHORTCUT_ABSENT_VALUE: str = "none"

# A shortcut record names one action and the combination it must own in the first
# field of its value; the task reads only that field, because the second field
# holds the combination the action ships with and the third its friendly name,
# which the running daemon reports and writes itself. A record copied from
# kglobalshortcutsrc therefore needs nothing removed from it. The records are
# applied to the running KGlobalAccel daemon, which holds the combinations in
# memory and writes the file itself: the shared client frees every named
# combination from whatever action holds it and gives it to the configured
# action. A combination the daemon does not report back is asked for again,
# because the state right after the first attempt can still belong to another
# action. An action the daemon does not know is asked for again, because kwin
# registers the actions of an enabled script when it re-reads its configuration
# and that registration can reach the daemon after the first call; a combination
# the client cannot read is reported at once, without waiting; and every
# combination the daemon still does not hold is written into the file as the
# fallback of the next login.
SHORTCUT_APPLY_ATTEMPTS: int = 5
SHORTCUT_APPLY_RETRY_DELAY_SECONDS: float = 3.0

# The component of the KGlobalAccel daemon that owns the script actions: its
# unique name, which is also the group their records carry in
# kglobalshortcutsrc, and its friendly name. With an action they form the action
# id the daemon answers.
KWIN_COMPONENT_UNIQUE: str = "kwin"
KWIN_COMPONENT_FRIENDLY: str = "KWin"

# The python3-dbus client that frees a keyboard combination from its current owner
# and gives it to a named action of the running KGlobalAccel daemon, by the task
# data section that carries it and its file name. The client is the shared one of
# the keyboard layout hotkeys, so the DBus encoding of a key list lives in one
# file only.
KGLOBALACCEL_CLIENT_SECTION_NAME: str = "kde_keyboard_setup"
KGLOBALACCEL_CLIENT_FILE_NAME: str = "apply_hotkeys.py"

# The python client that prints the id of every virtual desktop, one per line, in
# position order; the directory under task_data that holds one directory per kwin
# script; and the Konsole profile shipped as a task data file. A body longer than
# five lines lives in a file like the clients rather than in the code.
DESKTOP_IDS_SCRIPT_FILE_NAME: str = "list_desktop_ids.py"
KWIN_SCRIPTS_DIR_NAME: str = "kwin"
KONSOLE_PROFILE_FILE_NAME: str = "Pyntara.profile"

# The XDG user directories, all folded into Downloads while Desktop stays; each
# maps an XDG_*_DIR variable to its target path.
USER_DIRS: dict[str, str] = {
    "XDG_DOCUMENTS_DIR": "$HOME/downloads",
    "XDG_MUSIC_DIR": "$HOME/downloads",
    "XDG_PICTURES_DIR": "$HOME/downloads",
    "XDG_PUBLICSHARE_DIR": "$HOME/downloads",
    "XDG_TEMPLATES_DIR": "$HOME/downloads",
    "XDG_VIDEOS_DIR": "$HOME/downloads",
}

# Prefix that runs a command as the desktop user, so the task reaches the session
# files the user owns; {username} is filled from the shared module.
RUNUSER_COMMAND: tuple[str, ...] = ("runuser", "-u", "{username}", "--")

# The type word of a KConfig flag: a record whose type is this word is written
# with --type bool, and the flag takes the same word as its argument.
KCONFIG_BOOL_TYPE: str = "bool"

# Vocabulary of the KConfig tools the task reads, writes and deletes the desktop
# configuration with: the two base calls carry the file as {file_name}, a group is
# selected with CONFIG_GROUP_FLAG, a key with CONFIG_KEY_FLAG, a boolean value
# with CONFIG_BOOL_TYPE_FLAG and a write that must reach a live owner with
# CONFIG_NOTIFY_FLAG.
KREADCONFIG_COMMAND: tuple[str, ...] = ("kreadconfig6", "--file", "{file_name}")
KWRITECONFIG_COMMAND: tuple[str, ...] = ("kwriteconfig6", "--file", "{file_name}")
CONFIG_GROUP_FLAG: tuple[str, ...] = ("--group", "{group}")
CONFIG_KEY_FLAG: tuple[str, ...] = ("--key", "{key}")
CONFIG_BOOL_TYPE_FLAG: tuple[str, ...] = ("--type", KCONFIG_BOOL_TYPE)
CONFIG_NOTIFY_FLAG: tuple[str, ...] = ("--notify",)
CONFIG_DELETE_FLAG: tuple[str, ...] = ("--delete",)

# Commands of the plasma-apply tools the task runs live when a desktop session
# exists, each carrying the value it applies; the value is written into the
# config file first, so a session is an improvement and never a requirement.
APPLY_LOOK_AND_FEEL_COMMAND: tuple[str, ...] = (
    "plasma-apply-lookandfeel",
    "-a",
    "{look_and_feel}",
)
APPLY_COLOR_SCHEME_COMMAND: tuple[str, ...] = (
    "plasma-apply-colorscheme",
    "{color_scheme}",
)
APPLY_CURSOR_THEME_COMMAND: tuple[str, ...] = (
    "plasma-apply-cursortheme",
    "{cursor_theme}",
)

# Commands of the file operations the task runs on the files it deploys for the
# user; {path}, {owner} and {file_mode} are filled in at the call site.
MKDIR_COMMAND: tuple[str, ...] = ("mkdir", "-p", "{path}")
CHOWN_COMMAND: tuple[str, ...] = ("chown", "{owner}", "{path}")
CHOWN_RECURSIVE_COMMAND: tuple[str, ...] = ("chown", "-R", "{owner}", "{path}")
CHMOD_COMMAND: tuple[str, ...] = ("chmod", "{file_mode}", "{path}")

# The KConfig values of the desktop, applied by the task as the desktop user: the
# file name under ~/.config, the group segments, the key and the string form of
# its value. Every value is applied only when it differs from the current one, so
# repeated runs skip matching values. A record with delete removes the key
# instead of writing it, and one with the type "bool" writes a flag.
KCONFIG_RECORDS: tuple[KconfigRecord, ...] = (
    KconfigRecord("kwinrc", ("TabBox",), "ActivitiesMode", "0"),
    KconfigRecord("kwinrc", ("TabBox",), "DesktopMode", "0"),
    KconfigRecord("kwinrc", ("TabBoxAlternative",), "LayoutName", "coverswitch"),
    KconfigRecord("kwinrc", ("TabBoxAlternative",), "ActivitiesMode", "0"),
    KconfigRecord("kwinrc", ("TabBoxAlternative",), "DesktopMode", "0"),
    KconfigRecord("kdeglobals", ("Sounds",), "Theme", "freedesktop"),
    KconfigRecord("kwinrc", ("Effect-login",), "FadeToBlack", "true", "bool"),
    KconfigRecord("kdeglobals", ("KDE",), "SingleClick", "true", "bool"),
    KconfigRecord("kdeglobals", ("KDE",), "contrast", "4"),
    KconfigRecord("kdeglobals", ("KDE",), "frameContrast", "0.2"),
    KconfigRecord("kcminputrc", ("Mouse",), "cursorSize", "72"),
    KconfigRecord("kwinrc", ("Desktops",), "Number", "4"),
    KconfigRecord("kwinrc", ("Plugins",), "blurEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "cubeEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "fallapartEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "hidecursorEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "invertEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "magiclampEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "mouseclickEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "mousemarkEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "sheetEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "slidebackEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "touchpointsEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "minimizeallEnabled", "true", "bool"),
    KconfigRecord("kwinrc", ("Plugins",), "squashEnabled", "false", "bool"),
    KconfigRecord("kwinrc", ("Effect-zoom",), "ZoomFactor", "1.15"),
    KconfigRecord("kwinrc", ("Effect-zoom",), "MousePointer", "1"),
    KconfigRecord("kwinrc", ("Effect-zoom",), "MouseTracking", "2"),
    KconfigRecord("kwinrc", ("Effect-zoom",), "PixelGridZoom", "10"),
    KconfigRecord("kwinrc", ("Effect-wobblywindows",), "AdvancedMode", "true", "bool"),
    KconfigRecord("kwinrc", ("Effect-wobblywindows",), "Drag", "98"),
    KconfigRecord("kwinrc", ("Effect-wobblywindows",), "MoveFactor", "3"),
    KconfigRecord("kwinrc", ("Effect-wobblywindows",), "Stiffness", "24"),
    KconfigRecord("kwinrc", ("Effect-wobblywindows",), "WobblynessLevel", "3"),
    KconfigRecord("kwinrc", ("Effect-mouseclick",), "LineWidth", "0.3"),
    KconfigRecord("kwinrc", ("Effect-mouseclick",), "RingCount", "7"),
    KconfigRecord("kwinrc", ("Effect-mouseclick",), "RingLife", "500"),
    KconfigRecord("kwinrc", ("Effect-mouseclick",), "RingSize", "128"),
    KconfigRecord("kwinrc", ("Effect-mouseclick",), "ShowText", "false", "bool"),
    KconfigRecord("kwinrc", ("Effect-mousemark",), "Arrowdrawcontrol", delete=True),
    KconfigRecord("kwinrc", ("Effect-mousemark",), "Arrowdrawshift", delete=True),
    KconfigRecord("kwinrc", ("Effect-mousemark",), "Freedrawshift", delete=True),
    KconfigRecord("kwinrc", ("Effect-cube",), "Background", "SkyBox"),
    KconfigRecord("kwinrc", ("Effect-cube",), "CubeFaceDisplacement", "0"),
    KconfigRecord("kwinrc", ("Effect-cube",), "DistanceFactor", "1.01"),
    KconfigRecord(
        "kwinrc",
        ("Effect-cube",),
        "SkyBox",
        "/usr/share/wallpapers/Path/contents/images/2560x1600.jpg",
    ),
    KconfigRecord("kwinrc", ("Effect-cube",), "InvertX", "false", "bool"),
    KconfigRecord("kwinrc", ("Effect-cube",), "InvertY", "false", "bool"),
    KconfigRecord("kwinrc", ("Desktops",), "Rows", "1"),
    KconfigRecord("kwinrc", ("Effect-scale",), "Duration", "200"),
    KconfigRecord("kwinrc", ("Effect-scale",), "InScale", "0.01"),
    KconfigRecord("kwinrc", ("Effect-scale",), "OutScale", "0.01"),
    KconfigRecord("kwinrc", ("Effect-shakecursor",), "Magnification", "4"),
    KconfigRecord("kwinrc", ("Effect-overview",), "BorderActivate", "9"),
    KconfigRecord(
        "kwinrc", ("MouseBindings",), "CommandAll2", "Activate, raise and move"
    ),
    KconfigRecord("kwinrc", ("MouseBindings",), "CommandAll3", "Resize"),
    KconfigRecord("kwinrc", ("MouseBindings",), "CommandAllKey", "Meta"),
    KconfigRecord("kwinrc", ("MouseBindings",), "CommandAllWheel", "Change Opacity"),
    KconfigRecord(
        "kwinrc", ("MouseBindings",), "CommandTitlebarWheel", "Change Opacity"
    ),
    KconfigRecord("kwinrc", ("NightColor",), "Active", "true", "bool"),
    KconfigRecord("kwinrc", ("NightColor",), "Mode", "DarkLight"),
    KconfigRecord("kwinrc", ("Windows",), "BorderSnapZone", "32"),
    KconfigRecord("kwinrc", ("Windows",), "CenterSnapZone", "1"),
    KconfigRecord("kwinrc", ("Windows",), "ElectricBorderTiling", "false", "bool"),
    KconfigRecord("kwinrc", ("Windows",), "ElectricBorders", "1"),
    KconfigRecord("kwinrc", ("Windows",), "Placement", "Random"),
    KconfigRecord("kwinrc", ("Windows",), "RollOverDesktops", "true", "bool"),
    KconfigRecord("kwinrc", ("Windows",), "SnapOnlyWhenOverlapping", "true", "bool"),
    KconfigRecord("kwinrc", ("Windows",), "WindowSnapZone", "32"),
    KconfigRecord("kwinrc", ("org.kde.kdecoration2",), "ButtonsOnLeft", "MSNE"),
    KconfigRecord("kwinrc", ("org.kde.kdecoration2",), "ButtonsOnRight", "HFBIAX"),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "ClearMouseMarks",
        "Meta+Shift+F11,Meta+Shift+F11,Clear Mouse Marks",
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "ClearLastMouseMark",
        "Meta+Shift+F12,Meta+Shift+F12,Clear Last Mouse Mark",
    ),
    KconfigRecord("kglobalshortcutsrc", ("kwin",), "Cube", "Meta+C,Meta+C,Toggle Cube"),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "MinimizeAll",
        "Meta+D,none,Minimize all windows",
    ),
    KconfigRecord(
        "kglobalshortcutsrc", ("kwin",), "Invert", "Meta+I,none,Toggle Invert Effect"
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "InvertWindow",
        "Meta+Ctrl+I,Meta+Ctrl+U,Toggle Invert Effect on Window",
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "manage activities",
        "none,none,Show Activity Switcher",
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("plasmashell",),
        "manage activities",
        "none,none,Show Activity Switcher",
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("KDE Keyboard Layout Switcher",),
        "Switch keyboard layout to Spanish",
        "Meta+E,none,Switch keyboard layout to Spanish",
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "Walk Through Windows",
        "Alt+Tab,none,Walk Through Windows",
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "Walk Through Windows (Reverse)",
        "Alt+Shift+Tab,none,Walk Through Windows (Reverse)",
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "Walk Through Windows Alternative",
        "Meta+Tab,none,Walk Through Windows Alternative",
    ),
    KconfigRecord(
        "kglobalshortcutsrc",
        ("kwin",),
        "Walk Through Windows Alternative (Reverse)",
        "Meta+Shift+Tab,none,Walk Through Windows Alternative (Reverse)",
    ),
    KconfigRecord(
        "kscreenlockerrc",
        ("Greeter", "LnF", "General"),
        "showMediaControls",
        "false",
        "bool",
    ),
    KconfigRecord(
        "kscreenlockerrc",
        ("Greeter", "Wallpaper", "org.kde.image", "General"),
        "Image",
        "file:///usr/share/wallpapers/Nexus/#day-night",
    ),
    KconfigRecord(
        "kscreenlockerrc",
        ("Greeter", "Wallpaper", "org.kde.image", "General"),
        "PreviewImage",
        "file:///usr/share/wallpapers/Nexus/#day-night",
    ),
    KconfigRecord("ksmserverrc", ("General",), "loginMode", "emptySession"),
    KconfigRecord("powerdevilrc", ("AC", "Display"), "DimDisplayIdleTimeoutSec", "840"),
    KconfigRecord(
        "powerdevilrc", ("AC", "Display"), "TurnOffDisplayIdleTimeoutSec", "900"
    ),
    KconfigRecord("powerdevilrc", ("AC", "Performance"), "PowerProfile", "performance"),
    KconfigRecord(
        "powerdevilrc", ("AC", "SuspendAndShutdown"), "AutoSuspendAction", "0"
    ),
    KconfigRecord(
        "powerdevilrc",
        ("AC", "SuspendAndShutdown"),
        "AutoSuspendIdleTimeoutSec",
        "599940",
    ),
    KconfigRecord("powerdevilrc", ("AC", "SuspendAndShutdown"), "LidAction", "0"),
    KconfigRecord(
        "powerdevilrc", ("Battery", "SuspendAndShutdown"), "AutoSuspendAction", "0"
    ),
    KconfigRecord("powerdevilrc", ("Battery", "SuspendAndShutdown"), "LidAction", "0"),
    KconfigRecord("powerdevilrc", ("BatteryManagement",), "BatteryLowLevel", "17"),
    KconfigRecord(
        "powerdevilrc", ("LowBattery", "Performance"), "PowerProfile", "power-saver"
    ),
    KconfigRecord(
        "powerdevilrc", ("LowBattery", "SuspendAndShutdown"), "LidAction", "0"
    ),
    KconfigRecord(
        "plasma-org.kde.plasma.desktop-appletsrc",
        ("Containments", "1", "Wallpaper", "org.kde.slideshow", "General"),
        "SlideInterval",
        "64740",
    ),
    KconfigRecord(
        "plasma-org.kde.plasma.desktop-appletsrc",
        ("Containments", "1", "Wallpaper", "org.kde.slideshow", "General"),
        "SlidePaths",
        "/usr/share/wallpapers/",
    ),
    KconfigRecord(
        "plasma-org.kde.plasma.desktop-appletsrc",
        ("Containments", "1"),
        "wallpaperplugin",
        "org.kde.slideshow",
    ),
    KconfigRecord(
        "systemsettingsrc",
        ("systemsettings_sidebar_mode",),
        "HighlightNonDefaultSettings",
        "true",
        "bool",
    ),
    KconfigRecord(
        "KDE/Sonnet.conf", ("General",), "autodetectLanguage", "true", "bool"
    ),
    KconfigRecord(
        "KDE/Sonnet.conf", ("General",), "checkerEnabledByDefault", "true", "bool"
    ),
    KconfigRecord("KDE/Sonnet.conf", ("General",), "defaultLanguage", "en_US"),
    KconfigRecord("KDE/Sonnet.conf", ("General",), "preferredLanguages", "en_US"),
    KconfigRecord(
        "kactivitymanagerd-pluginsrc",
        ("Plugin-org.kde.ActivityManager.Resources.Scoring",),
        "keep-history-for",
        "99999",
    ),
    KconfigRecord("konsolerc", ("Desktop Entry",), "DefaultProfile", "Pyntara.profile"),
    KconfigRecord("kwinrc", ("Effect-mousemark",), "Width", "3"),
    KconfigRecord("dolphinrc", ("ContentDisplay",), "DirectorySizeMode", "ContentSize"),
    KconfigRecord(
        "dolphinrc", ("ContentDisplay",), "RecursiveDirectorySizeLimit", "12"
    ),
    KconfigRecord(
        "dolphinrc",
        ("General",),
        "HomeUrl",
        f"{{{HOME_PLACEHOLDER_NAME}}}/Downloads",
    ),
    KconfigRecord("dolphinrc", ("General",), "AutoExpandFolders", "true", "bool"),
    KconfigRecord("dolphinrc", ("General",), "BrowseThroughArchives", "true", "bool"),
    KconfigRecord("dolphinrc", ("General",), "OpenNewTabAfterLastTab", "true", "bool"),
    KconfigRecord("dolphinrc", ("General",), "RememberOpenedTabs", "false", "bool"),
    KconfigRecord("dolphinrc", ("General",), "ShowFullPath", "true", "bool"),
    KconfigRecord("dolphinrc", ("General",), "ShowFullPathInTitlebar", "true", "bool"),
    KconfigRecord("dolphinrc", ("General",), "ShowStatusBar", "FullWidth"),
    KconfigRecord("dolphinrc", ("General",), "ShowToolTips", "true", "bool"),
    KconfigRecord("dolphinrc", ("InformationPanel",), "dateFormat", "ShortFormat"),
    KconfigRecord("kdeglobals", ("KDE",), "ShowDeleteCommand", "false", "bool"),
    KconfigRecord(
        "kdeglobals",
        ("PreviewSettings",),
        "EnableRemoteFolderThumbnail",
        "false",
        "bool",
    ),
    KconfigRecord("kdeglobals", ("PreviewSettings",), "MaximumRemoteSize", "0"),
    KconfigRecord("kdeglobals", ("PreviewSettings",), "MaximumSize", "32505856"),
    KconfigRecord("kiorc", ("Confirmations",), "ConfirmDelete", "true", "bool"),
    KconfigRecord("kiorc", ("Confirmations",), "ConfirmEmptyTrash", "true", "bool"),
    KconfigRecord("kiorc", ("Confirmations",), "ConfirmTrash", "true", "bool"),
    KconfigRecord("kiorc", ("Executable scripts",), "behaviourOnLaunch", "alwaysAsk"),
    KconfigRecord(
        "kservicemenurc", ("Show",), "compressfileitemaction", "true", "bool"
    ),
    KconfigRecord("kservicemenurc", ("Show",), "extractfileitemaction", "true", "bool"),
    KconfigRecord("kservicemenurc", ("Show",), "forgetfileitemaction", "true", "bool"),
    KconfigRecord("kservicemenurc", ("Show",), "hidefileitemaction", "false", "bool"),
    KconfigRecord("kservicemenurc", ("Show",), "installFont", "true", "bool"),
    KconfigRecord(
        "kservicemenurc",
        ("Show",),
        "kactivitymanagerd_fileitem_linking_plugin",
        "true",
        "bool",
    ),
    KconfigRecord(
        "kservicemenurc", ("Show",), "kdeconnectfileitemaction", "true", "bool"
    ),
    KconfigRecord("kservicemenurc", ("Show",), "kio-admin", "true", "bool"),
    KconfigRecord("kservicemenurc", ("Show",), "makefileactions", "true", "bool"),
    KconfigRecord("kservicemenurc", ("Show",), "mountisoaction", "true", "bool"),
    KconfigRecord(
        "kservicemenurc", ("Show",), "movetonewfolderitemaction", "true", "bool"
    ),
    KconfigRecord(
        "kservicemenurc", ("Show",), "plasmavaultfileitemaction", "true", "bool"
    ),
    KconfigRecord("kservicemenurc", ("Show",), "runInKonsole", "true", "bool"),
    KconfigRecord(
        "kservicemenurc", ("Show",), "setfoldericonitemaction", "true", "bool"
    ),
    KconfigRecord("kservicemenurc", ("Show",), "sharefileitemaction", "true", "bool"),
    KconfigRecord(
        "kservicemenurc", ("Show",), "slideshowfileitemaction", "true", "bool"
    ),
    KconfigRecord("kservicemenurc", ("Show",), "tagsfileitemaction", "true", "bool"),
    KconfigRecord(
        "kservicemenurc", ("Show",), "wallpaperfileitemaction", "true", "bool"
    ),
    KconfigRecord(
        "ktrashrc",
        (f"{{{HOME_PLACEHOLDER_NAME}}}/.local/share/Trash",),
        "Days",
        "211",
    ),
    KconfigRecord(
        "ktrashrc",
        (f"{{{HOME_PLACEHOLDER_NAME}}}/.local/share/Trash",),
        "Percent",
        "23",
    ),
    KconfigRecord(
        "ktrashrc",
        (f"{{{HOME_PLACEHOLDER_NAME}}}/.local/share/Trash",),
        "UseSizeLimit",
        "true",
        "bool",
    ),
    KconfigRecord(
        "ktrashrc",
        (f"{{{HOME_PLACEHOLDER_NAME}}}/.local/share/Trash",),
        "UseTimeLimit",
        "true",
        "bool",
    ),
    KconfigRecord(
        "ktrashrc",
        (f"{{{HOME_PLACEHOLDER_NAME}}}/.local/share/Trash",),
        "LimitReachedAction",
        "2",
    ),
    KconfigRecord("katerc", ("General",), "Allow Tab Scrolling", "true", "bool"),
    KconfigRecord("katerc", ("General",), "Auto Hide Tabs", "false", "bool"),
    KconfigRecord("katerc", ("General",), "Close After Last", "false", "bool"),
    KconfigRecord(
        "katerc", ("General",), "Close documents with window", "true", "bool"
    ),
    KconfigRecord("katerc", ("General",), "Cycle To First Tab", "true", "bool"),
    KconfigRecord("katerc", ("General",), "Days Meta Infos", "180"),
    KconfigRecord("katerc", ("General",), "Diagnostics Limit", "12000"),
    KconfigRecord("katerc", ("General",), "Diff Show Style", "0"),
    KconfigRecord("katerc", ("General",), "Elide Tab Text", "false", "bool"),
    KconfigRecord("katerc", ("General",), "Enable Context ToolView", "false", "bool"),
    KconfigRecord("katerc", ("General",), "Expand Tabs", "false", "bool"),
    KconfigRecord(
        "katerc",
        ("General",),
        "Icon size for left and right sidebar buttons",
        "32",
    ),
    KconfigRecord("katerc", ("General",), "Modified Notification", "false", "bool"),
    KconfigRecord("katerc", ("General",), "Mouse back button action", "0"),
    KconfigRecord("katerc", ("General",), "Mouse forward button action", "0"),
    KconfigRecord(
        "katerc", ("General",), "Open New Tab To The Right Of Current", "false", "bool"
    ),
    KconfigRecord("katerc", ("General",), "Output History Limit", "100"),
    KconfigRecord("katerc", ("General",), "Output With Date", "false", "bool"),
    KconfigRecord("katerc", ("General",), "Recent File List Entry Count", "99"),
    KconfigRecord(
        "katerc", ("General",), "Restore Window Configuration", "true", "bool"
    ),
    KconfigRecord("katerc", ("General",), "SDI Mode", "false", "bool"),
    KconfigRecord("katerc", ("General",), "Save Meta Infos", "true", "bool"),
    KconfigRecord("katerc", ("General",), "Show Full Path in Title", "true", "bool"),
    KconfigRecord("katerc", ("General",), "Show Menu Bar", "true", "bool"),
    KconfigRecord("katerc", ("General",), "Show Status Bar", "true", "bool"),
    KconfigRecord(
        "katerc", ("General",), "Show Symbol In Navigation Bar", "true", "bool"
    ),
    KconfigRecord("katerc", ("General",), "Show Tab Bar", "true", "bool"),
    KconfigRecord("katerc", ("General",), "Show Tabs Close Button", "true", "bool"),
    KconfigRecord("katerc", ("General",), "Show Url Nav Bar", "true", "bool"),
    KconfigRecord("katerc", ("General",), "Show output view for message type", "1"),
    KconfigRecord(
        "katerc", ("General",), "Show text for left and right sidebar", "false", "bool"
    ),
    KconfigRecord(
        "katerc", ("General",), "Show welcome view for new window", "false", "bool"
    ),
    KconfigRecord("katerc", ("General",), "Startup Session", "manual"),
    KconfigRecord("katerc", ("General",), "Stash new unsaved files", "true", "bool"),
    KconfigRecord(
        "katerc", ("General",), "Stash unsaved file changes", "false", "bool"
    ),
    KconfigRecord(
        "katerc", ("General",), "Sync section size with tab positions", "false", "bool"
    ),
    KconfigRecord(
        "katerc", ("General",), "Tab Double Click New Document", "true", "bool"
    ),
    KconfigRecord(
        "katerc", ("General",), "Tab Middle Click Close Document", "true", "bool"
    ),
    KconfigRecord("katerc", ("General",), "Tabbar Tab Limit", "0"),
    KconfigRecord(
        "katerc",
        ("KTextEditor Document",),
        "Allow End of Line Detection",
        "true",
        "bool",
    ),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Auto Detect Indent", "true", "bool"
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor Document",),
        "Auto Reload If Any External Changes Occurs",
        "false",
        "bool",
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor Document",),
        "Auto Reload If State Is In Version Control",
        "true",
        "bool",
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "Auto Save", "false", "bool"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Auto Save Interval", "0"),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Auto Save On Focus Out", "false", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "BOM", "false", "bool"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Backup Local", "false", "bool"),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Backup Remote", "false", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "Backup Suffix", "~"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Camel Cursor", "false", "bool"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Encoding", "UTF-8"),
    KconfigRecord("katerc", ("KTextEditor Document",), "End of Line", "0"),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Indent On Backspace", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "Indent On Tab", "true", "bool"),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Indent On Text Paste", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "Indentation Mode", "normal"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Indentation Width", "4"),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Keep Extra Spaces", "false", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "Line Length Limit", "10000"),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Newline at End of File", "true", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "On-The-Fly Spellcheck", "true", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Overwrite Mode", "false", "bool"
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor Document",),
        "PageUp/PageDown Moves Cursor",
        "false",
        "bool",
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "Remove Spaces", "1"),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "ReplaceTabsDyn", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "Show Spaces", "2"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Show Tabs", "true", "bool"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Smart Home", "true", "bool"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Swap File Mode", "1"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Swap Sync Interval", "15"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Tab Handling", "2"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Tab Width", "4"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Trailing Marker Size", "1"),
    KconfigRecord(
        "katerc", ("KTextEditor Document",), "Use Editor Config", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor Document",), "Word Wrap", "false", "bool"),
    KconfigRecord("katerc", ("KTextEditor Document",), "Word Wrap Column", "80"),
    KconfigRecord(
        "katerc", ("KTextEditor Renderer",), "Animate Bracket Matching", "false", "bool"
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor Renderer",),
        "Auto Color Theme Selection",
        "true",
        "bool",
    ),
    KconfigRecord("katerc", ("KTextEditor Renderer",), "Color Theme", "Breeze Dark"),
    KconfigRecord("katerc", ("KTextEditor Renderer",), "Line Height Multiplier", "1"),
    KconfigRecord(
        "katerc", ("KTextEditor Renderer",), "Show Indentation Lines", "true", "bool"
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor Renderer",),
        "Show Whole Bracket Expression",
        "false",
        "bool",
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor Renderer",),
        "Text Font",
        "Hack,10,-1,7,400,0,0,0,0,0,0,0,0,0,0,1",
    ),
    KconfigRecord(
        "katerc", ("KTextEditor Renderer",), "Word Wrap Marker", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Allow Mark Menu", "true", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Auto Brackets", "false", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Auto Center Lines", "0"),
    KconfigRecord("katerc", ("KTextEditor View",), "Auto Completion", "true", "bool"),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Auto Completion Preselect First Entry",
        "true",
        "bool",
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Backspace Remove Composed Characters",
        "false",
        "bool",
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Bookmark Menu Sorting", "1"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Bracket Match Preview", "false", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Chars To Enclose Selection", "<>(){}[]'\""
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Cycle Through Bookmarks", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Default Mark Type", "1"),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Disable bracket match highlight if inactive",
        "false",
        "bool",
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Disable current line highlight if inactive",
        "false",
        "bool",
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Dynamic Word Wrap", "true", "bool"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Dynamic Word Wrap Align Indent", "80"
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Dynamic Word Wrap At Static Marker",
        "false",
        "bool",
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Dynamic Word Wrap Indicators", "1"),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Dynamic Wrap not at word boundaries",
        "false",
        "bool",
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Enable Accessibility", "true", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Enable Tab completion", "false", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Enter To Insert Completion", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Fold First Line", "false", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Folding Bar", "true", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Folding Preview", "true", "bool"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Hide cursor if inactive", "false", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Icon Bar", "false", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Input Mode", "0"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Keyword Completion", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Line Modification", "true", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Line Numbers", "true", "bool"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Max Clipboard History Entries", "777"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Maximum Search History Size", "100"
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Mouse Paste At Cursor Position",
        "false",
        "bool",
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Multiple Cursor Modifier", "134217728"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Persistent Selection", "false", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Scroll Bar Marks", "false", "bool"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Scroll Bar Mini Map All", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Scroll Bar Mini Map Width", "60"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Scroll Bar MiniMap", "true", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Scroll Bar Preview", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Scroll Past End", "false", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Search/Replace Flags", "140"),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Shoe Line Ending Type in Statusbar",
        "false",
        "bool",
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Show Documentation With Completion",
        "true",
        "bool",
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Show File Encoding", "true", "bool"
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Show Folding Icons On Hover Only",
        "true",
        "bool",
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Show Line Count", "true", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Show Scrollbars", "0"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Show Statusbar Dictionary", "true", "bool"
    ),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Show Statusbar Highlighting Mode",
        "true",
        "bool",
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Show Statusbar Input Mode", "true", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Show Statusbar Line Column", "true", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Show Statusbar Tab Settings", "true", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Show Word Count", "true", "bool"),
    KconfigRecord("katerc", ("KTextEditor View",), "Smart Copy Cut", "true", "bool"),
    KconfigRecord(
        "katerc",
        ("KTextEditor View",),
        "Statusbar Line Column Compact Mode",
        "true",
        "bool",
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Text Drag And Drop", "true", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Vi Input Mode Steal Keys", "false", "bool"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Vi Relative Line Numbers", "false", "bool"
    ),
    KconfigRecord("katerc", ("KTextEditor View",), "Word Completion", "true", "bool"),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Word Completion Minimal Word Length", "3"
    ),
    KconfigRecord(
        "katerc", ("KTextEditor View",), "Word Completion Remove Tail", "true", "bool"
    ),
    KconfigRecord("katerc", ("Konsole",), "AutoSyncronizeMode", "0"),
    KconfigRecord("katerc", ("Konsole",), "KonsoleEscKeyBehaviour", "true", "bool"),
    KconfigRecord("katerc", ("Konsole",), "KonsoleEscKeyExceptions", "vi,vim,nvim,git"),
    KconfigRecord("katerc", ("Konsole",), "RemoveExtension", "false", "bool"),
    KconfigRecord("katerc", ("Konsole",), "SetEditor", "false", "bool"),
    KconfigRecord("katerc", ("filetree",), "showFullPathOnRoots", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "AutoHover", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "AutoImport", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "CompletionDocumentation", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "CompletionParens", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "Diagnostics", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "FormatOnSave", "false", "bool"),
    KconfigRecord("katerc", ("lspclient",), "HighlightGoto", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "HighlightSymbol", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "IncrementalSync", "false", "bool"),
    KconfigRecord("katerc", ("lspclient",), "InlayHints", "false", "bool"),
    KconfigRecord("katerc", ("lspclient",), "Messages", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "ReferencesDeclaration", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "SemanticHighlighting", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "ShowCompletions", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "SignatureHelp", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "SymbolDetails", "false", "bool"),
    KconfigRecord("katerc", ("lspclient",), "SymbolExpand", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "SymbolSort", "false", "bool"),
    KconfigRecord("katerc", ("lspclient",), "SymbolTree", "true", "bool"),
    KconfigRecord("katerc", ("lspclient",), "TypeFormatting", "false", "bool"),
)

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "KCONFIG_BOOL_TYPE",
    "USERNAME_PLACEHOLDER_NAME",
    "HOME_PLACEHOLDER_NAME",
    "COLOR_SCHEME",
    "LOOK_AND_FEEL",
    "LOOK_AND_FEEL_LIGHT",
    "AUTOMATIC_LOOK_AND_FEEL",
    "CURSOR_THEME",
    "CURSOR_THEME_LIGHT",
    "NUMLOCK_ON_BOOT",
    "TOUCHPAD_CLICK_METHOD",
    "VIRTUAL_KEYBOARD_ENABLED",
    "VIRTUAL_KEYBOARD_INPUT_METHOD",
    "VIRTUAL_KEYBOARD_LOCALES",
    "KWIN_RELOAD_COMMAND",
    "PYTHON_SCRIPT_COMMAND",
    "KWIN_BUS_NAME",
    "VIRTUAL_DESKTOP_MANAGER_OBJECT_PATH",
    "VIRTUAL_DESKTOP_MANAGER_INTERFACE_NAME",
    "VIRTUAL_DESKTOPS_PROPERTY_NAME",
    "VIRTUAL_DESKTOP_COUNT_PROPERTY_NAME",
    "DBUS_PROPERTIES_INTERFACE_NAME",
    "KWIN_DESKTOP_COUNT_COMMAND",
    "KWIN_DESKTOP_CREATE_COMMAND",
    "KWIN_DESKTOP_REMOVE_COMMAND",
    "SDDM_CONF_FILE",
    "SDDM_THEME_CONF_FILE",
    "USER_CONFIG_DIR",
    "USER_KWIN_SCRIPTS_DIR",
    "USER_LOOK_AND_FEEL_DIR",
    "USER_PLACES_FILE",
    "USER_DIRS_FILE",
    "KONSOLE_PROFILE_PATH",
    "SCRIPT_FILE_MODE",
    "DEFAULT_FILE_MODE",
    "SYSTEM_LOOK_AND_FEEL_DIR",
    "THEME_DEFAULTS_DIR",
    "SDDM_AUTOLOGIN_USER",
    "SDDM_AUTOLOGIN_SESSION",
    "SDDM_THEME",
    "SDDM_THEME_CURSOR_SIZE",
    "SDDM_THEME_CURSOR_THEME",
    "SDDM_THEME_FONT",
    "PLACES_HIDDEN",
    "PLACES_NAMESPACES",
    "PLACES_METADATA_OWNER",
    "KDEGLOBALS_FILE_NAME",
    "KCMINPUTRC_FILE_NAME",
    "KWINRC_FILE_NAME",
    "PLASMA_KEYBOARD_FILE_NAME",
    "GENERAL_GROUP",
    "KDE_GROUP",
    "MOUSE_GROUP",
    "KEYBOARD_GROUP",
    "WAYLAND_GROUP",
    "VIRTUAL_KEYBOARD_GROUP",
    "PLUGINS_GROUP",
    "DESKTOPS_GROUP",
    "TOUCHPAD_GROUP_ROOT",
    "TOUCHPAD_DEVICE_WORD",
    "LOOK_AND_FEEL_PACKAGE_KEY",
    "COLOR_SCHEME_KEY",
    "AUTOMATIC_LOOK_AND_FEEL_KEY",
    "AUTOMATIC_LOOK_AND_FEEL_IDLE_INTERVAL_KEY",
    "NUMLOCK_KEY",
    "INPUT_METHOD_KEY",
    "INPUT_METHOD_LOCALES_KEY",
    "CURSOR_THEME_KEY",
    "CLICK_METHOD_KEY",
    "DESKTOP_COUNT_KEY",
    "NUMLOCK_VALUES",
    "CLICK_METHOD_VALUES",
    "AUTOMATIC_THEME_SWITCH_IDLE_INTERVAL",
    "PLACES_ROOT_TAG",
    "PLACES_BOOKMARK_TAG",
    "PLACES_TITLE_TAG",
    "PLACES_METADATA_PATH",
    "PLACES_METADATA_OWNER_ATTRIBUTE",
    "PLACES_HIDDEN_ELEMENT",
    "PLACES_HIDDEN_VALUE",
    "KWIN_SCRIPTS",
    "KWIN_SCRIPT_FILES",
    "KWIN_SCRIPT_HOTKEYS",
    "KWIN_SCRIPT_ACTIONS",
    "SHORTCUT_ABSENT_VALUE",
    "SHORTCUT_APPLY_ATTEMPTS",
    "SHORTCUT_APPLY_RETRY_DELAY_SECONDS",
    "KWIN_COMPONENT_UNIQUE",
    "KWIN_COMPONENT_FRIENDLY",
    "KGLOBALACCEL_CLIENT_SECTION_NAME",
    "KGLOBALACCEL_CLIENT_FILE_NAME",
    "DESKTOP_IDS_SCRIPT_FILE_NAME",
    "KWIN_SCRIPTS_DIR_NAME",
    "KONSOLE_PROFILE_FILE_NAME",
    "USER_DIRS",
    "RUNUSER_COMMAND",
    "KREADCONFIG_COMMAND",
    "KWRITECONFIG_COMMAND",
    "CONFIG_GROUP_FLAG",
    "CONFIG_KEY_FLAG",
    "CONFIG_BOOL_TYPE_FLAG",
    "CONFIG_NOTIFY_FLAG",
    "CONFIG_DELETE_FLAG",
    "APPLY_LOOK_AND_FEEL_COMMAND",
    "APPLY_COLOR_SCHEME_COMMAND",
    "APPLY_CURSOR_THEME_COMMAND",
    "MKDIR_COMMAND",
    "CHOWN_COMMAND",
    "CHOWN_RECURSIVE_COMMAND",
    "CHMOD_COMMAND",
    "KCONFIG_RECORDS",
)
