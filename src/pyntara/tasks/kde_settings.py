"""Task kde_settings: apply the KDE appearance and input settings.

The task applies the configured dark appearance as the target user: the
color scheme that turns every Qt and KDE window dark and the global theme
that covers the whole desktop (panel, widgets, window decorations, icons).
Both values are applied with the plasma-apply tools through runuser, so
the config files stay owned by that user. The task also applies the input
and keyboard settings as KConfig values with kwriteconfig6: the NumLock
state on startup, the touchpad preferences (to every touchpad found) and
the Wayland virtual keyboard. The cursor theme is applied with
plasma-apply-cursortheme after the kconfig records, so it wins over the
theme default that the day and night switch writes. The dark and light
themes are copied into the user look and feel directory with their
configured cursor themes in the defaults, so the switch applies the right
cursor with the theme itself. When a desktop
session is running the changes apply immediately; without a session the
tools still write the config and the settings apply after the next login.
The task is idempotent: it reads the current values with kreadconfig6 and
applies only what differs. When automatic_look_and_feel is set, the task
enables the native KDE day and night theme switch instead of applying a
fixed theme, so a run never fights the switch. Missing packages (the
provider of the plasma-apply tools and the KConfig tools) are installed
first.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

from pyntara.config import KdeSettingsConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_package_once,
    package_is_installed,
    run_command,
    session_bus_address,
    trim_whitespace,
)

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
REPO_ROOT = Path(__file__).resolve().parents[3]
KONSOLE_PROFILE_TEMPLATE = (
    REPO_ROOT / "task_data" / "kde_settings" / "Pyntara.profile"
)
# The KWin scripts the task installs and enables. Each script is a
# directory under the kwin task data root with metadata.json and
# contents/code/main.js, copied into the user local share kwin scripts
# directory; enabling lives in kwinrc [Plugins].
KWIN_SCRIPTS_TEMPLATE_ROOT = REPO_ROOT / "task_data" / "kde_settings" / "kwin"
KWIN_SCRIPTS: tuple[str, ...] = ("window-grow-shrink", "window-restore-tracker")
KWIN_SCRIPT_FILES: tuple[str, ...] = ("metadata.json", "contents/code/main.js")
USER_KWIN_SCRIPTS_REL = Path(".local/share/kwin/scripts")
# The keyboard combinations the KWin scripts claim for themselves. The
# task sets them aggressively: any action that owns one of them,
# wherever it lives, is cleared so the script grabs the key.
KWIN_SCRIPT_HOTKEYS: tuple[str, ...] = ("Meta+Ctrl+Up", "Meta+Ctrl+Down")
# The script actions that own the hotkeys; they are never cleared as
# conflicts with themselves.
KWIN_SCRIPT_ACTIONS: tuple[str, ...] = (
    "Grow Window by 5px",
    "Shrink Window by 5px",
)

# The kdeglobals groups and keys that carry the applied theme values.
GENERAL_GROUP: tuple[str, ...] = ("General",)
KDE_GROUP: tuple[str, ...] = ("KDE",)
# The KConfig files the input and keyboard settings live in and their
# groups.
KCINPUTRC_FILE = "kcminputrc"
KWINRC_FILE = "kwinrc"
PLASMA_KEYBOARD_RC = "plasmakeyboardrc"
MOUSE_GROUP: tuple[str, ...] = ("Mouse",)
# The XDG user directory file and the Konsole profile target path, both
# under the target user config and local share directories.
USER_DIRS_FILE = "user-dirs.dirs"
KONSOLE_PROFILE_REL = ".local/share/konsole/Pyntara.profile"
USER_PLACES_REL = ".local/share/user-places.xbel"
# The system and user look and feel directories: the user copy of a theme
# wins over the system one, so the task copies the themes whose defaults
# carry the configured cursor themes.
SYSTEM_LOOK_AND_FEEL_DIR = Path("/usr/share/plasma/look-and-feel")
USER_LOOK_AND_FEEL_REL = ".local/share/plasma/look-and-feel"
THEME_DEFAULTS_REL = Path("contents") / "defaults"
NUMLOCK_GROUP: tuple[str, ...] = ("Keyboard",)
WAYLAND_GROUP: tuple[str, ...] = ("Wayland",)
VIRTUAL_KEYBOARD_GROUP: tuple[str, ...] = ("General",)
# The NumLock and click method values as stored in the KConfig files.
NUMLOCK_VALUES: dict[str, str] = {"on": "0", "off": "1", "unchanged": "2"}
CLICK_METHOD_VALUES: dict[str, str] = {
    "clickfinger": "1",
    "clickareas": "2",
    "none": "0",
}
# The idle wait, in minutes, before the native day and night theme switch
# applies its new theme; recorded from the user's manual tuning.
AUTOMATIC_THEME_SWITCH_IDLE_INTERVAL = "99"


def _as_user_command(cfg: KdeSettingsConfig, command: list[str]) -> list[str]:
    """Prefix a command with runuser so it runs as the target user."""

    return ["runuser", "-u", cfg.username, "--", *command]


def _home_env(cfg: KdeSettingsConfig) -> dict[str, str]:
    """Environment that points the KDE tools at the target user home."""

    return {"HOME": cfg.home_dir}


def _kreadconfig(
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    timeout: float,
) -> str:
    """Current value of one KConfig key, or an empty string when unset."""

    command = ["kreadconfig6", "--file", file_name]
    for segment in group_segments:
        command.extend(["--group", segment])
    command.extend(["--key", key])
    result = run_command(
        _as_user_command(cfg, command),
        extra_env=_home_env(cfg),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return trim_whitespace(result.stdout)


def _kwriteconfig(
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    value: str,
    *,
    timeout: float,
    bool_value: bool,
) -> None:
    """Write one KConfig key with kwriteconfig6 as the target user."""

    command = ["kwriteconfig6", "--file", file_name]
    for segment in group_segments:
        command.extend(["--group", segment])
    command.extend(["--key", key])
    if bool_value:
        command.append("--type")
        command.append("bool")
    command.append(value)
    run_command(
        _as_user_command(cfg, command),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )


def _delete_kconfig_key(
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    *,
    timeout: float,
) -> None:
    """Delete one KConfig key with kwriteconfig6 as the target user."""

    command = ["kwriteconfig6", "--file", file_name]
    for segment in group_segments:
        command.extend(["--group", segment])
    command.extend(["--key", key, "--delete"])
    run_command(
        _as_user_command(cfg, command),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )


def _sync_config_value(
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    target: str,
    *,
    timeout: float,
    force: bool,
    bool_value: bool,
) -> bool:
    """Write the KConfig key when it differs; True when a write happened."""

    current = _kreadconfig(cfg, file_name, group_segments, key, timeout)
    if not force and current == target:
        return False
    _kwriteconfig(
        cfg,
        file_name,
        group_segments,
        key,
        target,
        timeout=timeout,
        bool_value=bool_value,
    )
    _log(f"set {file_name} {key}: {target}")
    return True


def _apply_env(cfg: KdeSettingsConfig, timeout: float) -> dict[str, str]:
    """Environment that lets the plasma-apply tools reach the session bus.

    The session bus address is read from the target user's kwin_wayland
    process when a desktop session is running, so the theme applies live;
    a missing session leaves the environment without the bus, the tools
    still write the config and the theme applies after the next login.
    """

    env = _home_env(cfg)
    bus = session_bus_address(cfg.username, timeout)
    if bus is not None:
        env["DBUS_SESSION_BUS_ADDRESS"] = bus
    return env


def _apply_look_and_feel(
    cfg: KdeSettingsConfig,
    *,
    env: dict[str, str],
    timeout: float,
    force: bool,
) -> bool:
    """Apply the configured global theme when it differs; True when applied."""

    current = _kreadconfig(cfg, "kdeglobals", KDE_GROUP, "LookAndFeelPackage", timeout)
    if not force and current == cfg.look_and_feel:
        return False
    run_command(
        _as_user_command(cfg, ["plasma-apply-lookandfeel", "-a", cfg.look_and_feel]),
        extra_env=env,
        timeout=timeout,
    )
    _log(f"applied global theme: {cfg.look_and_feel}")
    return True


def _apply_color_scheme(
    cfg: KdeSettingsConfig,
    *,
    env: dict[str, str],
    timeout: float,
    force: bool,
) -> bool:
    """Apply the configured color scheme when it differs; True when applied."""

    current = _kreadconfig(cfg, "kdeglobals", GENERAL_GROUP, "ColorScheme", timeout)
    if not force and current == cfg.color_scheme:
        return False
    run_command(
        _as_user_command(cfg, ["plasma-apply-colorscheme", cfg.color_scheme]),
        extra_env=env,
        timeout=timeout,
    )
    _log(f"applied color scheme: {cfg.color_scheme}")
    return True


def _apply_automatic_look_and_feel(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Enable the native day and night theme switch; True when changed.

    When automatic_look_and_feel is set, the task turns on the KDE switch
    that alternates the light and dark themes by the time of day and does
    not apply a fixed theme itself, so a run never fights the switch.
    """

    if not cfg.automatic_look_and_feel:
        return False
    changed = _sync_config_value(
        cfg,
        "kdeglobals",
        KDE_GROUP,
        "AutomaticLookAndFeel",
        "true",
        timeout=timeout,
        force=force,
        bool_value=True,
    )
    changed |= _sync_config_value(
        cfg,
        "kdeglobals",
        KDE_GROUP,
        "AutomaticLookAndFeelIdleInterval",
        AUTOMATIC_THEME_SWITCH_IDLE_INTERVAL,
        timeout=timeout,
        force=force,
        bool_value=False,
    )
    return changed


def _apply_numlock(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the NumLock startup state; True when changed."""

    return _sync_config_value(
        cfg,
        KCINPUTRC_FILE,
        NUMLOCK_GROUP,
        "NumLock",
        NUMLOCK_VALUES[cfg.numlock_on_boot],
        timeout=timeout,
        force=force,
        bool_value=False,
    )


def _touchpad_groups(text: str) -> list[tuple[str, ...]]:
    """The [Libinput][...][name] groups whose device name ends with Touchpad.

    The numeric libinput ids in a group are machine-specific, so the task
    matches devices by name; a name that ends with Touchpad identifies a
    touchpad on any target hardware.
    """

    groups: list[tuple[str, ...]] = []
    current: tuple[str, ...] = ()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = tuple(part for part in line[1:-1].split("][") if part)
            if (
                len(current) >= 4
                and current[0] == "Libinput"
                and current[-1].endswith("Touchpad")
            ):
                groups.append(current)
    return groups


def _apply_touchpad(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the touchpad preferences to every touchpad found.

    The touchpad group ids are machine-specific and the target device is
    unknown, so the task applies the preferences to every libinput group
    whose device name ends with Touchpad; no touchpad is not an error.
    """

    kcminputrc = Path(cfg.home_dir) / ".config" / KCINPUTRC_FILE
    try:
        groups = _touchpad_groups(kcminputrc.read_text(encoding="utf-8"))
    except OSError:
        _log(f"no {KCINPUTRC_FILE} found, touchpad settings left as is")
        return False
    if not groups:
        _log("no touchpad found, touchpad settings left as is")
        return False
    changed = False
    for group in groups:
        changed |= _sync_config_value(
            cfg,
            KCINPUTRC_FILE,
            group,
            "ClickMethod",
            CLICK_METHOD_VALUES[cfg.touchpad_click_method],
            timeout=timeout,
            force=force,
            bool_value=False,
        )
        changed |= _sync_config_value(
            cfg,
            KCINPUTRC_FILE,
            group,
            "DisableEventsOnExternalMouse",
            "true" if cfg.touchpad_disable_on_external_mouse else "false",
            timeout=timeout,
            force=force,
            bool_value=True,
        )
    return changed


def _apply_virtual_keyboard(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write or remove the Wayland virtual keyboard; True when changed.

    The input method key in kwinrc is written with its plain name: the
    kwriteconfig6 tool escapes the [$e] flag the GUI writes, and
    kreadconfig6 reads both forms through the plain key, so the plain form
    keeps the comparison idempotent. The enabled locales go into
    plasmakeyboardrc.
    """

    changed = False
    if cfg.virtual_keyboard_enabled:
        changed |= _sync_config_value(
            cfg,
            KWINRC_FILE,
            WAYLAND_GROUP,
            "InputMethod",
            cfg.virtual_keyboard_input_method,
            timeout=timeout,
            force=force,
            bool_value=False,
        )
        changed |= _sync_config_value(
            cfg,
            PLASMA_KEYBOARD_RC,
            VIRTUAL_KEYBOARD_GROUP,
            "enabledLocales",
            ",".join(cfg.virtual_keyboard_locales),
            timeout=timeout,
            force=force,
            bool_value=False,
        )
    else:
        current = _kreadconfig(cfg, KWINRC_FILE, WAYLAND_GROUP, "InputMethod", timeout)
        if force or current:
            _delete_kconfig_key(
                cfg, KWINRC_FILE, WAYLAND_GROUP, "InputMethod", timeout=timeout
            )
            _log("removed Wayland input method")
            changed = True
    return changed


def _apply_cursor_theme(
    cfg: KdeSettingsConfig,
    *,
    env: dict[str, str],
    timeout: float,
    force: bool,
) -> bool:
    """Apply the configured cursor theme; True when changed.

    The cursor theme is applied with plasma-apply-cursortheme, which sets
    the live cursor and writes cursorTheme into kcminputrc. The native
    day and night theme switch overwrites cursorTheme with the theme
    default whenever it applies a look and feel, so the task applies the
    cursor theme after the kconfig records to win over that overwrite.
    """

    current = _kreadconfig(cfg, KCINPUTRC_FILE, MOUSE_GROUP, "cursorTheme", timeout)
    if not force and current == cfg.cursor_theme:
        return False
    run_command(
        _as_user_command(cfg, ["plasma-apply-cursortheme", cfg.cursor_theme]),
        extra_env=env,
        timeout=timeout,
    )
    _log(f"applied cursor theme: {cfg.cursor_theme}")
    return True


def _apply_theme_cursor_overrides(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Copy the configured themes with their cursor defaults; True when changed.

    The day and night theme switch overwrites cursorTheme in kcminputrc
    with the theme default whenever it applies a look and feel, so the
    task copies the dark and light themes into the user look and feel
    directory, where a copy wins over the system one, and writes the
    configured cursor theme into the copy defaults. The switch then
    applies the right cursor with the theme itself. A missing system
    theme is not an error: the packages install it before the task runs.
    """

    changed = False
    for look_and_feel, cursor_theme in (
        (cfg.look_and_feel, cfg.cursor_theme),
        (cfg.look_and_feel_light, cfg.cursor_theme_light),
    ):
        source = SYSTEM_LOOK_AND_FEEL_DIR / look_and_feel
        if not source.is_dir():
            _log(f"no system theme {look_and_feel}, cursor override skipped")
            continue
        target = Path(cfg.home_dir) / USER_LOOK_AND_FEEL_REL / look_and_feel
        if not target.is_dir():
            shutil.copytree(source, target)
            run_command(
                ["chown", "-R", f"{cfg.username}:{cfg.username}", str(target)],
                timeout=timeout,
            )
            _log(f"copied theme {look_and_feel} into the user look and feel directory")
        changed |= _sync_config_value(
            cfg,
            str(target / THEME_DEFAULTS_REL),
            ("kcminputrc", "Mouse"),
            "cursorTheme",
            cursor_theme,
            timeout=timeout,
            force=force,
            bool_value=False,
        )
    return changed


def _apply_kconfig_records(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Apply every configured kconfig record; True when any changed.

    Each value record is read with kreadconfig6 and written only when it
    differs, so matching records are skipped; a delete record removes the
    key when it is present. Force mode writes and removes regardless.
    """

    changed = False
    for record in cfg.kconfig:
        if record.delete:
            current = _kreadconfig(
                cfg, record.file, record.group, record.key, timeout
            )
            if not force and not current:
                continue
            _delete_kconfig_key(
                cfg, record.file, record.group, record.key, timeout=timeout
            )
            _log(f"removed {record.file} {record.key}")
            changed = True
            continue
        changed |= _sync_config_value(
            cfg,
            record.file,
            record.group,
            record.key,
            record.value,
            timeout=timeout,
            force=force,
            bool_value=record.type == "bool",
        )
    return changed


def _shortcut_primaries(cfg: KdeSettingsConfig) -> dict[str, list[str]]:
    """Map every shortcut primary key to the shortcut keys that own it.

    The shortcut records are the kconfig records of kglobalshortcutsrc
    whose value is in the KDE primary,alternate,description format; the
    primary is the first comma field. A delete record owns no key.
    """

    owned: dict[str, list[str]] = {}
    for record in cfg.kconfig:
        if record.file != "kglobalshortcutsrc" or record.delete:
            continue
        if "," not in record.value:
            continue
        primary = record.value.split(",", 1)[0]
        owned.setdefault(primary, []).append(record.key)
    return owned


def _clear_shortcut_conflicts(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
) -> bool:
    """Unbind every action that shares a primary key with a configured
    shortcut; True when any was cleared.

    A configured shortcut must win over any other action on the target
    machine, wherever that action lives. The scan reads kglobalshortcutsrc
    and rewrites every value whose primary field matches a configured
    primary and whose key is not one of the configured shortcuts to
    none,none, so the key stops belonging to that action. The rewrite runs
    through kwriteconfig6 as the target user, keeping the file owned by
    that user. A missing file is not an error.
    """

    owned = _shortcut_primaries(cfg)
    if not owned:
        return False
    path = Path(cfg.home_dir) / ".config" / "kglobalshortcutsrc"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    group: tuple[str, ...] = ()
    changed = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            group = tuple(part for part in stripped[1:-1].split("][") if part)
            continue
        key, sep, value = stripped.partition("=")
        if not sep or "," not in value:
            continue
        primary = value.split(",", 1)[0]
        if primary not in owned or key in owned[primary]:
            continue
        fields = value.split(",")
        cleared = ",".join(["none", "none"] + fields[2:])
        _kwriteconfig(
            cfg,
            "kglobalshortcutsrc",
            group,
            key,
            cleared,
            timeout=timeout,
            bool_value=False,
        )
        _log(f"cleared conflicting shortcut {key}: {primary}")
        changed = True
    return changed


def _user_dirs_merged(current: str, user_dirs: dict[str, str]) -> str:
    """current with the configured XDG dirs replaced in place.

    A line whose key equals a configured XDG variable is replaced by the
    configured directive, so its position is kept and a matching value
    leaves the line untouched; a configured directive missing from the
    file is appended. Every other line, comments and foreign keys, is
    preserved.
    """

    directives = {key: f'{key}="{value}"' for key, value in user_dirs.items()}
    seen: set[str] = set()
    merged: list[str] = []
    for line in current.splitlines():
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key in directives:
            merged.append(directives[key])
            seen.add(key)
        else:
            merged.append(line)
    for key, directive in directives.items():
        if key not in seen:
            merged.append(directive)
    return "\n".join(merged) + "\n"


def _write_user_file(
    cfg: KdeSettingsConfig,
    rel_path: str,
    content: str,
    *,
    mode: str,
    timeout: float,
    force: bool,
) -> bool:
    """Write one user-owned file as the target user; True when written.

    The directory is created as the target user, the content is written by
    the root process and then chowned and chmodded to the target user, so
    the file keeps the user ownership a desktop config file needs. A file
    that already holds the content is skipped.
    """

    target = Path(cfg.home_dir) / rel_path
    if not force and target.is_file():
        try:
            if target.read_text(encoding="utf-8") == content:
                return False
        except OSError:
            pass
    run_command(
        _as_user_command(cfg, ["mkdir", "-p", str(target.parent)]),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )
    # The user mkdir above owns the directory; this direct creation is a
    # no-op when it succeeded and a fallback for a read-only fixture.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_command(
        ["chown", f"{cfg.username}:{cfg.username}", str(target)],
        timeout=timeout,
    )
    run_command(["chmod", mode, str(target)], timeout=timeout)
    _log(f"wrote {target}")
    return True


def _apply_kwin_scripts(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Install and enable the KWin scripts; True when anything changed.

    Each script template under the kwin task data directory is written
    into the user local share kwin scripts directory as the target user
    and enabled in kwinrc [Plugins]. Files are written only when their
    content differs, so repeated runs skip matching scripts. A script
    whose template is missing is skipped entirely, so no dangling
    kwinrc enable is written.
    """

    changed = False
    for script in KWIN_SCRIPTS:
        templates = {
            rel_file: KWIN_SCRIPTS_TEMPLATE_ROOT / script / rel_file
            for rel_file in KWIN_SCRIPT_FILES
        }
        if any(not template.is_file() for template in templates.values()):
            _log(f"no kwin script template for {script}, {script} left as is")
            continue
        for rel_file, template in templates.items():
            content = template.read_text(encoding="utf-8")
            changed |= _write_user_file(
                cfg,
                str(USER_KWIN_SCRIPTS_REL / script / rel_file),
                content,
                mode="0644",
                timeout=timeout,
                force=force,
            )
        changed |= _sync_config_value(
            cfg,
            KWINRC_FILE,
            ("Plugins",),
            f"{script}Enabled",
            "true",
            timeout=timeout,
            force=force,
            bool_value=True,
        )
    return changed


def _script_hotkey_owners(
    text: str,
) -> list[tuple[tuple[str, ...], str, str]]:
    """The records in kglobalshortcutsrc that own a script hotkey.

    Every record whose primary or alternate key matches one of the
    combinations the KWin scripts claim is returned with its group, key
    and description, so the task can clear it from any action, whatever
    process registered it. The scripts' own actions are never returned.
    """

    owners: list[tuple[tuple[str, ...], str, str]] = []
    group: tuple[str, ...] = ()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            group = tuple(part for part in stripped[1:-1].split("][") if part)
            continue
        key, sep, value = stripped.partition("=")
        if not sep or "," not in value:
            continue
        if key in KWIN_SCRIPT_ACTIONS:
            continue
        fields = value.split(",")
        primary = fields[0].strip()
        alternate = fields[1].strip() if len(fields) > 1 else ""
        if (
            primary not in KWIN_SCRIPT_HOTKEYS
            and alternate not in KWIN_SCRIPT_HOTKEYS
        ):
            continue
        description = fields[2] if len(fields) > 2 else ""
        owners.append((group, key, description))
    return owners


def _release_hotkeys_live(
    cfg: KdeSettingsConfig,
    targets: list[tuple[str, str]],
    *,
    env: dict[str, str],
    timeout: float,
) -> None:
    """Ask the running KGlobalAccel daemon to release the hotkeys.

    The config rewrite alone only applies at the next session start; the
    running daemon holds the keys in memory, so it must release them for
    the change to apply live. The call runs through python3-dbus as the
    target user, the package the task installs.
    """

    code = (
        "import dbus\n"
        "import sys\n"
        "bus = dbus.SessionBus()\n"
        "obj = bus.get_object('org.kde.kglobalaccel', '/kglobalaccel')\n"
        "iface = dbus.Interface(obj, 'org.kde.KGlobalAccel')\n"
        "empty = dbus.Array([], signature='(ai)')\n"
        "for index in range(1, len(sys.argv), 2):\n"
        "    group = sys.argv[index]\n"
        "    action = sys.argv[index + 1]\n"
        "    iface.setForeignShortcutKeys([group, action, group, action], empty)\n"
    )
    command = ["python3", "-c", code]
    for group, action in targets:
        command.extend([group, action])
    run_command(
        _as_user_command(cfg, command),
        extra_env=env,
        timeout=timeout,
    )
    _log(f"released {len(targets)} hotkey owners in the running daemon")


def _free_script_hotkeys(
    cfg: KdeSettingsConfig,
    *,
    env: dict[str, str],
    timeout: float,
) -> bool:
    """Clear every action that owns a script hotkey; True when changed.

    The keyboard combinations the KWin scripts claim are set
    aggressively: any action that owns one of them, wherever it lives,
    is cleared, so the script grabs the key when it registers. The
    records are rewritten as the target user; when a desktop session is
    running the daemon releases the keys live through python3-dbus.
    """

    path = Path(cfg.home_dir) / ".config" / "kglobalshortcutsrc"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    owners = _script_hotkey_owners(text)
    if not owners:
        return False
    changed = False
    targets: list[tuple[str, str]] = []
    for group, key, description in owners:
        _kwriteconfig(
            cfg,
            "kglobalshortcutsrc",
            group,
            key,
            f"none,none,{description}" if description else "none,none",
            timeout=timeout,
            bool_value=False,
        )
        _log(f"cleared {key} from {group} for the kwin script hotkeys")
        if group:
            targets.append((group[0], key))
        changed = True
    if targets and "DBUS_SESSION_BUS_ADDRESS" in env:
        _release_hotkeys_live(cfg, targets, env=env, timeout=timeout)
    return changed


def _places_xbel_hidden(current: str, hidden: set[str]) -> str | None:
    """current with IsHidden=true for the hidden places; None when unchanged.

    The Dolphin Places panel file user-places.xbel marks a hidden system
    place by an IsHidden element in its KDE metadata. The match runs on
    the bookmark title, so no machine-specific id or device uuid enters
    the task. The file is re-serialized as XML only when a marker was
    added or changed; a run that changed nothing returns None so the task
    never rewrites the file over formatting differences alone.
    """

    ElementTree.register_namespace(
        "bookmark", "http://freedesktop.org/standards/desktop-bookmarks"
    )
    ElementTree.register_namespace("kdepriv", "http://www.kde.org/kdepriv")
    ElementTree.register_namespace(
        "mime", "http://freedesktop.org/standards/shared-mime-info"
    )
    root = ElementTree.fromstring(current)
    changed = False
    for bookmark in root.findall("bookmark"):
        if bookmark.findtext("title") not in hidden:
            continue
        for metadata in bookmark.findall("info/metadata"):
            if metadata.get("owner") != "http://www.kde.org":
                continue
            marker = metadata.find("IsHidden")
            if marker is None:
                ElementTree.SubElement(metadata, "IsHidden").text = "true"
                changed = True
            elif marker.text != "true":
                marker.text = "true"
                changed = True
    if not changed:
        return None
    header = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xbel>\n'
    return header + ElementTree.tostring(root, encoding="unicode")


def _apply_places_hidden(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Hide the configured system places in the Dolphin Places panel.

    The Places panel lives in user-places.xbel under the user local share
    directory, a plain XML file the desktop session owns. The task matches
    the configured hidden titles in the existing file and adds the
    IsHidden marker to their KDE metadata, leaving every other entry, the
    automatic device separators and the user bookmarks, untouched. A
    missing file is not an error: the desktop creates it at the first
    login, and the next run applies the hiding.
    """

    if not cfg.places_hidden:
        return False
    path = Path(cfg.home_dir) / USER_PLACES_REL
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        _log("no user-places.xbel found, Places hiding applies after first login")
        return False
    content = _places_xbel_hidden(current, set(cfg.places_hidden))
    if content is None:
        return False
    return _write_user_file(
        cfg, USER_PLACES_REL, content, mode="0600", timeout=timeout, force=force
    )


def _apply_user_dirs(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the configured XDG user directories; True when changed."""

    path = Path(cfg.home_dir) / ".config" / USER_DIRS_FILE
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    content = _user_dirs_merged(current, cfg.user_dirs)
    return _write_user_file(
        cfg,
        f".config/{USER_DIRS_FILE}",
        content,
        mode="0600",
        timeout=timeout,
        force=force,
    )


def _apply_konsole_profile(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the Pyntara Konsole profile; True when changed.

    The template is rendered with the target home directory, so the profile
    points at the right Downloads directory on any target machine.
    """

    try:
        template = KONSOLE_PROFILE_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        _log("no konsole profile template, profile left as is")
        return False
    content = template.replace("{home_dir}", cfg.home_dir)
    return _write_user_file(
        cfg,
        KONSOLE_PROFILE_REL,
        content,
        mode="0600",
        timeout=timeout,
        force=force,
    )


def _system_kreadconfig(
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    timeout: float,
) -> str:
    """Current value of one system KConfig key, read as the root process."""

    command = ["kreadconfig6", "--file", file_name]
    for segment in group_segments:
        command.extend(["--group", segment])
    command.extend(["--key", key])
    result = run_command(command, check=False, capture=True, timeout=timeout)
    return trim_whitespace(result.stdout)


def _system_kwriteconfig(
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    value: str,
    *,
    timeout: float,
) -> None:
    """Write one system KConfig key as the root process."""

    command = ["kwriteconfig6", "--file", file_name]
    for segment in group_segments:
        command.extend(["--group", segment])
    command.extend(["--key", key, value])
    run_command(command, timeout=timeout)


def _sync_system_value(
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    target: str,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write a system KConfig key when it differs; True when written."""

    current = _system_kreadconfig(file_name, group_segments, key, timeout)
    if not force and current == target:
        return False
    _system_kwriteconfig(file_name, group_segments, key, target, timeout=timeout)
    _log(f"set {file_name} {key}: {target}")
    return True


def _apply_sddm(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the SDDM autologin and theme; True when any changed.

    The values go into the system files /etc/sddm.conf and
    /etc/sddm.conf.d/20-kubuntu.conf as the root process, so they apply to
    the login screen on every boot.
    """

    changed = False
    for key, value in (
        ("User", cfg.sddm_autologin_user),
        ("Session", cfg.sddm_autologin_session),
    ):
        changed |= _sync_system_value(
            "/etc/sddm.conf",
            ("Autologin",),
            key,
            value,
            timeout=timeout,
            force=force,
        )
    for key, value in (
        ("Current", cfg.sddm_theme),
        ("CursorSize", cfg.sddm_theme_cursor_size),
        ("CursorTheme", cfg.sddm_theme_cursor_theme),
        ("Font", cfg.sddm_theme_font),
    ):
        changed |= _sync_system_value(
            "/etc/sddm.conf.d/20-kubuntu.conf",
            ("Theme",),
            key,
            value,
            timeout=timeout,
            force=force,
        )
    return changed


def _reload_kwin(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    env: dict[str, str],
) -> str | None:
    """Reload the kwin configuration; error text or None.

    Runs after the Wayland input method changed so kwin re-reads kwinrc.
    A missing session bus is not an error: the setting applies at login.
    """

    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        _log("no desktop session found, input method applies after login")
        return None
    try:
        run_command(
            _as_user_command(cfg, list(cfg.kwin_reload_command)),
            extra_env=env,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot reload kwin: {exc}"
    _log("reloaded kwin configuration")
    return None


def _apply_desktop_count_live(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    env: dict[str, str],
) -> str | None:
    """Apply the configured desktop count through the DBus API; error or None.

    KWin reads the desktop count from kwinrc only at session start, so a
    kwin reconfigure does not apply a changed Number. This function reads
    the target count from the kconfig records and creates or removes
    desktops through the VirtualDesktopManager DBus API to match it live.
    A missing session bus is not an error: the count applies at the next
    login.
    """

    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        return None
    target = None
    for record in cfg.kconfig:
        if record.file == "kwinrc" and record.group == ("Desktops",) and record.key == "Number":
            target = int(record.value)
            break
    if target is None:
        return None
    try:
        result = run_command(
            _as_user_command(
                cfg,
                [
                    "qdbus6",
                    "org.kde.KWin",
                    "/VirtualDesktopManager",
                    "org.kde.KWin.VirtualDesktopManager.count",
                ],
            ),
            extra_env=env,
            timeout=timeout,
            capture=True,
        )
        current = int(trim_whitespace(result.stdout))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        return f"cannot read desktop count: {exc}"
    if current == target:
        return None
    if current < target:
        for _ in range(current, target):
            try:
                run_command(
                    _as_user_command(
                        cfg,
                        [
                            "qdbus6",
                            "org.kde.KWin",
                            "/VirtualDesktopManager",
                            "org.kde.KWin.VirtualDesktopManager.createDesktop",
                            "0",
                            "",
                        ],
                    ),
                    extra_env=env,
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                return f"cannot create desktop: {exc}"
        _log(f"created {target - current} desktops, live count now {target}")
    else:
        result = run_command(
            _as_user_command(
                cfg,
                [
                    "qdbus6",
                    "org.kde.KWin",
                    "/VirtualDesktopManager",
                    "org.kde.KWin.VirtualDesktopManager.desktops",
                ],
            ),
            extra_env=env,
            timeout=timeout,
            capture=True,
        )
        ids = trim_whitespace(result.stdout).split("\n")
        for desktop_id in ids[-current + target:]:
            try:
                run_command(
                    _as_user_command(
                        cfg,
                        [
                            "qdbus6",
                            "org.kde.KWin",
                            "/VirtualDesktopManager",
                            "org.kde.KWin.VirtualDesktopManager.removeDesktop",
                            desktop_id,
                        ],
                    ),
                    extra_env=env,
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                return f"cannot remove desktop: {exc}"
        _log(f"removed {current - target} desktops, live count now {target}")
    return None


def task(ctx: Context) -> TaskResult:
    """Apply the dark appearance and the input and keyboard settings.

    The goal is reached when every configured value already matches and the
    packages are installed; the task then returns changed=False. Otherwise
    it installs missing packages and applies the differing values as the
    target user: the global theme first, then the color scheme so the
    configured scheme wins, then the NumLock state, the touchpad
    preferences, the Wayland virtual keyboard, the configured kconfig
    values, the theme cursor overrides that let the day and night switch
    apply the configured cursors, and the cursor theme last, so it wins
    over the theme default the switch writes. When automatic_look_and_feel
    is set, the theme is not applied directly: the task enables the native
    day and night switch instead, so a run never fights the switch. Any
    failure is returned as an error TaskResult.
    """

    cfg = ctx.config.kde_settings
    timeout = ctx.config.engine.command_timeout_seconds
    force = "kde_settings" in ctx.force_tasks
    changed = False

    for package in cfg.packages:
        if package_is_installed(package, timeout):
            continue
        _log(f"installing {package}")
        ok, error = install_package_once(package, timeout)
        if not ok:
            return TaskResult(
                success=False, error=f"cannot install {package}: {error}"
            )
        changed = True

    run_command(
        _as_user_command(cfg, ["mkdir", "-p", str(Path(cfg.home_dir) / ".config")]),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )
    apply_env = _apply_env(cfg, timeout)
    if "DBUS_SESSION_BUS_ADDRESS" not in apply_env:
        _log("no desktop session found, settings apply after login")

    settings_changed = False
    virtual_keyboard_changed = False
    kwin_scripts_changed = False
    try:
        if cfg.automatic_look_and_feel:
            settings_changed |= _apply_automatic_look_and_feel(
                cfg, timeout=timeout, force=force
            )
        else:
            settings_changed |= _apply_look_and_feel(
                cfg, env=apply_env, timeout=timeout, force=force
            )
            settings_changed |= _apply_color_scheme(
                cfg, env=apply_env, timeout=timeout, force=force
            )
        settings_changed |= _apply_numlock(cfg, timeout=timeout, force=force)
        settings_changed |= _apply_touchpad(cfg, timeout=timeout, force=force)
        virtual_keyboard_changed = _apply_virtual_keyboard(
            cfg, timeout=timeout, force=force
        )
        settings_changed |= virtual_keyboard_changed
        settings_changed |= _apply_kconfig_records(
            cfg, timeout=timeout, force=force
        )
        settings_changed |= _apply_theme_cursor_overrides(
            cfg, timeout=timeout, force=force
        )
        settings_changed |= _apply_cursor_theme(
            cfg, env=apply_env, timeout=timeout, force=force
        )
        settings_changed |= _clear_shortcut_conflicts(cfg, timeout=timeout)
        kwin_scripts_changed = _apply_kwin_scripts(
            cfg, timeout=timeout, force=force
        )
        settings_changed |= kwin_scripts_changed
        settings_changed |= _free_script_hotkeys(
            cfg, env=apply_env, timeout=timeout
        )
        settings_changed |= _apply_user_dirs(cfg, timeout=timeout, force=force)
        settings_changed |= _apply_konsole_profile(
            cfg, timeout=timeout, force=force
        )
        settings_changed |= _apply_places_hidden(
            cfg, timeout=timeout, force=force
        )
        settings_changed |= _apply_sddm(cfg, timeout=timeout, force=force)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(success=False, error=f"cannot apply KDE settings: {exc}")
    changed |= settings_changed

    kwinrc_changed = any(
        record.file == "kwinrc" for record in cfg.kconfig
    ) and settings_changed
    if virtual_keyboard_changed or kwinrc_changed or kwin_scripts_changed:
        reload_error = _reload_kwin(cfg, timeout=timeout, env=apply_env)
        if reload_error is not None:
            return TaskResult(success=False, error=reload_error)

    desktop_error = _apply_desktop_count_live(cfg, timeout=timeout, env=apply_env)
    if desktop_error is not None:
        return TaskResult(success=False, error=desktop_error)

    if not changed:
        return TaskResult(success=True, changed=False, message="already configured")
    return TaskResult(
        success=True,
        changed=True,
        message="KDE appearance and input settings configured",
    )
