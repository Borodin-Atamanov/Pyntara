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
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

from pyntara.config import EngineConfig, KdeSettingsConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_package_once,
    package_is_installed,
    run_command,
    session_environment,
    task_data_dir,
    trim_whitespace,
)

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
# The KWin scripts the task installs and enables live as directories under
# task_data/kde_settings/kwin in the clone, one directory per script; their
# names and the files each one carries come from the config, and the root
# comes from the context.
# The embedded DBus client that prints the id of every virtual desktop,
# one per line, in position order. The desktop list is a DBus property
# of structs (position, id, name); qdbus6 cannot render that type, so the
# task reads the ids through python3-dbus.
_DESKTOP_IDS_CLIENT = (
    "import dbus\n"
    "bus = dbus.SessionBus()\n"
    "obj = bus.get_object('org.kde.KWin', '/VirtualDesktopManager')\n"
    "props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')\n"
    "data = props.Get('org.kde.KWin.VirtualDesktopManager', 'desktops')\n"
    "for entry in data:\n"
    "    fields = [getattr(part, 'pyobject', part) for part in entry]\n"
    "    print(fields[1])\n"
)


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


def _notify_flag(
    cfg: KdeSettingsConfig, file_name: str, env: dict[str, str] | None
) -> list[str]:
    """The kwriteconfig6 --notify flag when the write reaches a live owner.

    The flag makes kwriteconfig6 emit the KConfig change DBus signal that
    kwin watches for kwinrc and kdeglobals, so the running kwin re-reads
    the file and applies the change live instead of only at the next
    login. A missing session bus makes the flag a harmless no-op, so it is
    added only when the caller passes the environment of a live session
    and the file has a live watcher.
    """

    if env is None:
        return []
    if file_name not in (cfg.kwinrc_file_name, cfg.kdeglobals_file_name):
        return []
    return ["--notify"]


def _kwriteconfig(
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    value: str,
    *,
    timeout: float,
    bool_value: bool,
    env: dict[str, str] | None = None,
) -> None:
    """Write one KConfig key with kwriteconfig6 as the target user.

    env is the live session environment when the caller knows a desktop
    session is running; its session bus makes the --notify flag reach the
    live owner of the file.
    """

    command = ["kwriteconfig6", "--file", file_name]
    for segment in group_segments:
        command.extend(["--group", segment])
    command.extend(["--key", key])
    if bool_value:
        command.append("--type")
        command.append("bool")
    command.append(value)
    command.extend(_notify_flag(cfg, file_name, env))
    write_env = env if env is not None else _home_env(cfg)
    run_command(
        _as_user_command(cfg, command),
        extra_env=write_env,
        timeout=timeout,
    )


def _delete_kconfig_key(
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> None:
    """Delete one KConfig key with kwriteconfig6 as the target user."""

    command = ["kwriteconfig6", "--file", file_name]
    for segment in group_segments:
        command.extend(["--group", segment])
    command.extend(["--key", key, "--delete"])
    command.extend(_notify_flag(cfg, file_name, env))
    write_env = env if env is not None else _home_env(cfg)
    run_command(
        _as_user_command(cfg, command),
        extra_env=write_env,
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
    env: dict[str, str] | None = None,
) -> bool:
    """Write the KConfig key when it differs; True when a write happened.

    env is forwarded to the write so a live session adds the --notify
    flag for files kwin watches.
    """

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
        env=env,
    )
    _log(f"set {file_name} {key}: {target}")
    return True


def _apply_env(
    cfg: KdeSettingsConfig, engine: EngineConfig
) -> dict[str, str] | None:
    """Environment that lets the plasma-apply tools reach the live session.

    The session variables are read from the session manager of the desktop
    user, so the theme applies live even when the run started over SSH
    without a desktop environment. None means no live session was found:
    the values are written into the config and apply after the next login,
    and a GUI tool is never started against a display that is not there.
    """

    session = session_environment(
        engine.desktop_username,
        command_template=engine.session_environment_command,
        keys=engine.session_environment_keys,
        bus_key=engine.session_bus_key,
        display_keys=engine.session_display_keys,
        timeout=engine.process_check_timeout_seconds,
    )
    if not session:
        return None
    env = _home_env(cfg)
    env.update(session)
    return env


def _run_appearance_tool_best_effort(
    cfg: KdeSettingsConfig,
    *,
    command: list[str],
    applied_message: str,
    timeout: float,
    env: dict[str, str] | None,
) -> None:
    """Run one plasma-apply tool live when a session is present.

    The plasma-apply tools are GUI programs that need the desktop display
    and abort on a machine without a live session (provisioning over SSH,
    before login) or when the display environment is missing. A missing
    session is therefore reported as a progress line and a failure of the
    tool is reported the same way, never raised, because the caller has
    already written the value into the config file and it applies at the
    next login.
    """

    if env is None:
        _log(f"no desktop session, {applied_message} applies at the next login")
        return
    try:
        run_command(_as_user_command(cfg, command), extra_env=env, timeout=timeout)
        _log(applied_message)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        _log(f"cannot apply {applied_message} live, it is set for the next login: {exc}")


def _guard_write(
    warnings: list[str] | None,
    description: str,
    call: Callable[[], bool],
) -> bool:
    """Run one independent value write; warn and continue on a failure.

    A single bad value inside a loop over independent values must not
    drop the remaining values: the failure is reported and the loop
    continues, so the task configures as much of the target as it can.
    warnings collects the failures when given.
    """

    try:
        return call()
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ) as exc:
        warning = f"cannot {description}: {exc}"
        _log(warning)
        if warnings is not None:
            warnings.append(warning)
        return False


def _apply_look_and_feel(
    cfg: KdeSettingsConfig,
    *,
    env: dict[str, str] | None,
    timeout: float,
    force: bool,
) -> bool:
    """Apply the configured global theme when it differs; True when applied.

    The theme is written into kdeglobals with kwriteconfig6 first, so it
    applies at the next login even without a session; plasma-apply-
    lookandfeel then switches the running session when one exists, as a
    best effort that never fails the task.
    """

    current = _kreadconfig(
        cfg, cfg.kdeglobals_file_name, cfg.kde_group, cfg.look_and_feel_package_key, timeout
    )
    if not force and current == cfg.look_and_feel:
        return False
    _sync_config_value(
        cfg,
        cfg.kdeglobals_file_name,
        cfg.kde_group,
        cfg.look_and_feel_package_key,
        cfg.look_and_feel,
        timeout=timeout,
        force=force,
        bool_value=False,
        env=env,
    )
    _run_appearance_tool_best_effort(
        cfg,
        command=["plasma-apply-lookandfeel", "-a", cfg.look_and_feel],
        applied_message=f"applied global theme: {cfg.look_and_feel}",
        timeout=timeout,
        env=env,
    )
    return True


def _apply_color_scheme(
    cfg: KdeSettingsConfig,
    *,
    env: dict[str, str] | None,
    timeout: float,
    force: bool,
) -> bool:
    """Apply the configured color scheme when it differs; True when applied.

    The scheme is written into kdeglobals with kwriteconfig6 first, so it
    applies at the next login even without a session; plasma-apply-
    colorscheme then switches the running session when one exists, as a
    best effort that never fails the task.
    """

    current = _kreadconfig(
        cfg, cfg.kdeglobals_file_name, cfg.general_group, cfg.color_scheme_key, timeout
    )
    if not force and current == cfg.color_scheme:
        return False
    _sync_config_value(
        cfg,
        cfg.kdeglobals_file_name,
        cfg.general_group,
        cfg.color_scheme_key,
        cfg.color_scheme,
        timeout=timeout,
        force=force,
        bool_value=False,
        env=env,
    )
    _run_appearance_tool_best_effort(
        cfg,
        command=["plasma-apply-colorscheme", cfg.color_scheme],
        applied_message=f"applied color scheme: {cfg.color_scheme}",
        timeout=timeout,
        env=env,
    )
    return True


def _apply_automatic_look_and_feel(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
    env: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> bool:
    """Enable the native day and night theme switch; True when changed.

    When automatic_look_and_feel is set, the task turns on the KDE switch
    that alternates the light and dark themes by the time of day and does
    not apply a fixed theme itself, so a run never fights the switch.
    """

    if not cfg.automatic_look_and_feel:
        return False
    changed = _guard_write(
        warnings,
        "enable the automatic theme switch",
        lambda: _sync_config_value(
            cfg,
            cfg.kdeglobals_file_name,
            cfg.kde_group,
            cfg.automatic_look_and_feel_key,
            cfg.kconfig_true_value,
            timeout=timeout,
            force=force,
            bool_value=True,
            env=env,
        ),
    )
    changed |= _guard_write(
        warnings,
        "set the automatic theme switch idle wait",
        lambda: _sync_config_value(
            cfg,
            cfg.kdeglobals_file_name,
            cfg.kde_group,
            cfg.automatic_look_and_feel_idle_interval_key,
            cfg.automatic_theme_switch_idle_interval,
            timeout=timeout,
            force=force,
            bool_value=False,
            env=env,
        ),
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
        cfg.kcminputrc_file_name,
        cfg.keyboard_group,
        cfg.numlock_key,
        cfg.numlock_values[cfg.numlock_on_boot],
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
    warnings: list[str] | None = None,
) -> bool:
    """Write the touchpad preferences to every touchpad found.

    The touchpad group ids are machine-specific and the target device is
    unknown, so the task applies the preferences to every libinput group
    whose device name ends with Touchpad; no touchpad is not an error. A
    group that fails to write is reported and the remaining groups still
    apply.
    """

    kcminputrc = (
        Path(cfg.home_dir) / cfg.user_config_dir / cfg.kcminputrc_file_name
    )
    try:
        groups = _touchpad_groups(kcminputrc.read_text(encoding="utf-8"))
    except OSError:
        _log(f"no {cfg.kcminputrc_file_name} found, touchpad settings left as is")
        return False
    if not groups:
        _log("no touchpad found, touchpad settings left as is")
        return False
    changed = False
    for group in groups:
        try:
            changed |= _sync_config_value(
                cfg,
                cfg.kcminputrc_file_name,
                group,
                cfg.click_method_key,
                cfg.click_method_values[cfg.touchpad_click_method],
                timeout=timeout,
                force=force,
                bool_value=False,
            )
            changed |= _sync_config_value(
                cfg,
                cfg.kcminputrc_file_name,
                group,
                cfg.touchpad_disable_external_mouse_key,
                (
                    cfg.kconfig_true_value
                    if cfg.touchpad_disable_on_external_mouse
                    else cfg.kconfig_false_value
                ),
                timeout=timeout,
                force=force,
                bool_value=True,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot set the touchpad preferences for {group[-1]}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    return changed


def _apply_virtual_keyboard(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
    env: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> bool:
    """Write or remove the Wayland virtual keyboard; True when changed.

    The input method key in kwinrc is written with its plain name: the
    kwriteconfig6 tool escapes the [$e] flag the GUI writes, and
    kreadconfig6 reads both forms through the plain key, so the plain form
    keeps the comparison idempotent. The enabled locales go into
    plasmakeyboardrc. Each write is guarded, so one failure is reported
    and the other value still applies.
    """

    changed = False
    if cfg.virtual_keyboard_enabled:
        changed |= _guard_write(
            warnings,
            "set the Wayland input method",
            lambda: _sync_config_value(
                cfg,
                cfg.kwinrc_file_name,
                cfg.wayland_group,
                cfg.input_method_key,
                cfg.virtual_keyboard_input_method,
                timeout=timeout,
                force=force,
                bool_value=False,
                env=env,
            ),
        )
        changed |= _guard_write(
            warnings,
            "set the virtual keyboard locales",
            lambda: _sync_config_value(
                cfg,
                cfg.plasma_keyboard_file_name,
                cfg.virtual_keyboard_group,
                cfg.input_method_locales_key,
                ",".join(cfg.virtual_keyboard_locales),
                timeout=timeout,
                force=force,
                bool_value=False,
                env=env,
            ),
        )
    else:
        current = _kreadconfig(
            cfg,
            cfg.kwinrc_file_name,
            cfg.wayland_group,
            cfg.input_method_key,
            timeout,
        )

        def remove_input_method() -> bool:
            _delete_kconfig_key(
                cfg,
                cfg.kwinrc_file_name,
                cfg.wayland_group,
                cfg.input_method_key,
                timeout=timeout,
                env=env,
            )
            _log("removed Wayland input method")
            return True

        if force or current:
            changed = _guard_write(
                warnings, "remove the Wayland input method", remove_input_method
            )
    return changed


def _apply_cursor_theme(
    cfg: KdeSettingsConfig,
    *,
    env: dict[str, str] | None,
    timeout: float,
    force: bool,
) -> bool:
    """Apply the configured cursor theme; True when changed.

    The theme is written into kcminputrc with kwriteconfig6 first, so it
    applies at the next login even without a session; plasma-apply-
    cursortheme then switches the running session when one exists, as a
    best effort that never fails the task. The native day and night theme
    switch overwrites cursorTheme with the theme default whenever it
    applies a look and feel, so the task applies the cursor theme after
    the kconfig records to win over that overwrite.
    """

    current = _kreadconfig(
        cfg, cfg.kcminputrc_file_name, cfg.mouse_group, cfg.cursor_theme_key, timeout
    )
    if not force and current == cfg.cursor_theme:
        return False
    _sync_config_value(
        cfg,
        cfg.kcminputrc_file_name,
        cfg.mouse_group,
        cfg.cursor_theme_key,
        cfg.cursor_theme,
        timeout=timeout,
        force=force,
        bool_value=False,
        env=env,
    )
    _run_appearance_tool_best_effort(
        cfg,
        command=["plasma-apply-cursortheme", cfg.cursor_theme],
        applied_message=f"applied cursor theme: {cfg.cursor_theme}",
        timeout=timeout,
        env=env,
    )
    return True


def _apply_theme_cursor_overrides(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
    warnings: list[str] | None = None,
) -> bool:
    """Copy the configured themes with their cursor defaults; True when changed.

    The day and night theme switch overwrites cursorTheme in kcminputrc
    with the theme default whenever it applies a look and feel, so the
    task copies the dark and light themes into the user look and feel
    directory, where a copy wins over the system one, and writes the
    configured cursor theme into the copy defaults. The switch then
    applies the right cursor with the theme itself. A missing system
    theme is not an error: the packages install it before the task runs.
    A theme that fails to copy or write is reported and the other theme
    still applies.
    """

    changed = False
    for look_and_feel, cursor_theme in (
        (cfg.look_and_feel, cfg.cursor_theme),
        (cfg.look_and_feel_light, cfg.cursor_theme_light),
    ):
        try:
            source = cfg.system_look_and_feel_dir / look_and_feel
            if not source.is_dir():
                _log(f"no system theme {look_and_feel}, cursor override skipped")
                continue
            target = Path(cfg.home_dir) / cfg.user_look_and_feel_dir / look_and_feel
            if not target.is_dir():
                shutil.copytree(source, target)
                run_command(
                    ["chown", "-R", f"{cfg.username}:{cfg.username}", str(target)],
                    timeout=timeout,
                )
                _log(
                    f"copied theme {look_and_feel} into the user look and feel "
                    "directory"
                )
            changed |= _sync_config_value(
                cfg,
                str(target / cfg.theme_defaults_dir),
                (cfg.kcminputrc_file_name, *cfg.mouse_group),
                cfg.cursor_theme_key,
                cursor_theme,
                timeout=timeout,
                force=force,
                bool_value=False,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot write the cursor override of {look_and_feel}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    return changed


def _apply_kconfig_records(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
    env: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> bool:
    """Apply every configured kconfig record; True when any changed.

    Each value record is read with kreadconfig6 and written only when it
    differs, so matching records are skipped; a delete record removes the
    key when it is present. Force mode writes and removes regardless. A
    record that fails through an external tool error is reported and the
    remaining records still apply, because one bad value must not stop
    the others; warnings collects the failures when given.
    """

    changed = False
    for record in cfg.kconfig:
        try:
            if record.delete:
                current = _kreadconfig(
                    cfg, record.file, record.group, record.key, timeout
                )
                if not force and not current:
                    continue
                _delete_kconfig_key(
                    cfg,
                    record.file,
                    record.group,
                    record.key,
                    timeout=timeout,
                    env=env,
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
                env=env,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot apply {record.file} {record.key}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    return changed


def _shortcut_primaries(cfg: KdeSettingsConfig) -> dict[str, list[str]]:
    """Map every shortcut primary key to the shortcut keys that own it.

    The shortcut records are the kconfig records of kglobalshortcutsrc
    whose value is in the KDE primary,alternate,description format; the
    primary is the first comma field. A delete record owns no key.
    """

    owned: dict[str, list[str]] = {}
    for record in cfg.kconfig:
        if record.file != cfg.global_shortcuts_file_name or record.delete:
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
    warnings: list[str] | None = None,
) -> bool:
    """Unbind every action that holds a configured key in a shortcut
    slot; True when any was cleared.

    A configured shortcut must win over any other action on the target
    machine, wherever that action lives. The scan reads kglobalshortcutsrc
    and rewrites each of the first two shortcut slots of a foreign value
    that equals a configured primary key to none, keeping the other slot
    and the description, so the key stops belonging to that action in any
    slot. The rewrite runs through kwriteconfig6 as the target user,
    keeping the file owned by that user. A missing file is not an error.
    An action that fails to clear is reported and the remaining actions
    still clear.
    """

    owned = _shortcut_primaries(cfg)
    if not owned:
        return False
    configured_keys = {
        record_key for record_keys in owned.values() for record_key in record_keys
    }
    path = Path(cfg.home_dir) / cfg.user_config_dir / cfg.global_shortcuts_file_name
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
        if not sep or "," not in value or key in configured_keys:
            continue
        fields = value.split(",")
        cleared = [
            "none" if index < 2 and field in owned else field
            for index, field in enumerate(fields)
        ]
        if cleared == fields:
            continue
        try:
            _kwriteconfig(
                cfg,
                cfg.global_shortcuts_file_name,
                group,
                key,
                ",".join(cleared),
                timeout=timeout,
                bool_value=False,
            )
            _log(f"cleared conflicting shortcut {key}: {value}")
            changed = True
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot clear the conflicting shortcut {key}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
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
    mode: int,
    timeout: float,
    force: bool,
) -> bool:
    """Write one user-owned file as the target user; True when written.

    The directory is created as the target user, the content is written by
    the root process and then chowned and chmodded to the target user with
    the given mode, so the file keeps the user ownership a desktop config
    file needs. A file that already holds the content is skipped.
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
    run_command(["chmod", f"{mode:04o}", str(target)], timeout=timeout)
    _log(f"wrote {target}")
    return True


def _apply_kwin_scripts(
    cfg: KdeSettingsConfig,
    scripts_template_root: Path,
    *,
    timeout: float,
    force: bool,
    env: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> bool:
    """Install and enable the KWin scripts; True when anything changed.

    Each script template under the kwin task data directory is written
    into the user local share kwin scripts directory as the target user
    and enabled in kwinrc [Plugins]. Files are written only when their
    content differs, so repeated runs skip matching scripts. A script
    whose template is missing is skipped entirely, so no dangling
    kwinrc enable is written. A script that fails to install or enable
    is reported and the remaining scripts still apply.
    """

    changed = False
    for script in cfg.kwin_scripts:
        try:
            templates = {
                rel_file: scripts_template_root / script / rel_file
                for rel_file in cfg.kwin_script_files
            }
            if any(not template.is_file() for template in templates.values()):
                _log(f"no kwin script template for {script}, {script} left as is")
                continue
            for rel_file, template in templates.items():
                content = template.read_text(encoding="utf-8")
                changed |= _write_user_file(
                    cfg,
                    str(cfg.user_kwin_scripts_dir / script / rel_file),
                    content,
                    mode=cfg.script_file_mode,
                    timeout=timeout,
                    force=force,
                )
            changed |= _sync_config_value(
                cfg,
                cfg.kwinrc_file_name,
                cfg.plugins_group,
                f"{script}Enabled",
                cfg.kconfig_true_value,
                timeout=timeout,
                force=force,
                bool_value=True,
                env=env,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot install or enable the kwin script {script}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    return changed


def _script_hotkey_owners(
    cfg: KdeSettingsConfig,
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
        if key in cfg.kwin_script_actions:
            continue
        fields = value.split(",")
        primary = fields[0].strip()
        alternate = fields[1].strip() if len(fields) > 1 else ""
        if (
            primary not in cfg.kwin_script_hotkeys
            and alternate not in cfg.kwin_script_hotkeys
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
    system_python: str,
) -> None:
    """Ask the running KGlobalAccel daemon to release the hotkeys.

    The config rewrite alone only applies at the next session start; the
    running daemon holds the keys in memory, so it must release them for
    the change to apply live. The call runs through python3-dbus as the
    target user, the package the task installs, under the system
    interpreter because the bindings install into the system Python only.
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
    command = [system_python, "-c", code]
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
    env: dict[str, str] | None,
    timeout: float,
    system_python: str,
    warnings: list[str] | None = None,
) -> bool:
    """Clear every action that owns a script hotkey; True when changed.

    The keyboard combinations the KWin scripts claim are set
    aggressively: any action that owns one of them, wherever it lives,
    is cleared, so the script grabs the key when it registers. The
    records are rewritten as the target user; when a desktop session is
    running the daemon releases the keys live through python3-dbus. An
    action that fails to clear or release is reported and the remaining
    actions still clear.
    """

    path = Path(cfg.home_dir) / cfg.user_config_dir / cfg.global_shortcuts_file_name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    owners = _script_hotkey_owners(cfg, text)
    if not owners:
        return False
    changed = False
    targets: list[tuple[str, str]] = []
    for group, key, description in owners:
        try:
            _kwriteconfig(
                cfg,
                cfg.global_shortcuts_file_name,
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
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot clear {key} from {group} for the script hotkeys: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    if targets and env is not None:
        try:
            _release_hotkeys_live(
                cfg, targets, env=env, timeout=timeout, system_python=system_python
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warning = f"cannot release the script hotkeys in the running daemon: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    return changed


def _places_prefix_addresses(
    cfg: KdeSettingsConfig,
) -> tuple[tuple[str, str], ...]:
    """The Places prefixes with the namespace address of each one."""

    return tuple(cfg.places_namespaces.items())


def _declare_missing_prefixes(cfg: KdeSettingsConfig, current: str) -> str:
    """current with the undeclared Places prefix declarations added.

    The prefixes the task deals with are declared on the root xbel tag
    when the document does not declare them yet, so a file that Dolphin
    wrote without namespace processing parses under the strict parser.
    A document that already declares the prefixes or has no xbel root
    tag is returned unchanged.
    """

    missing = [
        f'xmlns:{prefix}="{uri}"'
        for prefix, uri in _places_prefix_addresses(cfg)
        if f"xmlns:{prefix}=" not in current
    ]
    if not missing:
        return current
    root_start = current.find("<xbel")
    if root_start == -1:
        return current
    tag_end = current.find(">", root_start)
    if tag_end == -1:
        return current
    injection = " " + " ".join(missing)
    return current[:tag_end] + injection + current[tag_end:]


def _places_xbel_hidden(
    cfg: KdeSettingsConfig, current: str, hidden: set[str]
) -> str | None:
    """current with IsHidden=true for the hidden places; None when unchanged.

    The Dolphin Places panel file user-places.xbel marks a hidden system
    place by an IsHidden element in its KDE metadata. The match runs on
    the bookmark title, so no machine-specific id or device uuid enters
    the task. The file is re-serialized as XML only when a marker was
    added or changed; a run that changed nothing returns None so the task
    never rewrites the file over formatting differences alone.
    """

    for prefix, namespace in _places_prefix_addresses(cfg):
        ElementTree.register_namespace(prefix, namespace)
    try:
        root = ElementTree.fromstring(current)
    except ElementTree.ParseError:
        root = ElementTree.fromstring(_declare_missing_prefixes(cfg, current))
    changed = False
    for bookmark in root.findall(cfg.places_bookmark_tag):
        if bookmark.findtext(cfg.places_title_tag) not in hidden:
            continue
        for metadata in bookmark.findall(cfg.places_metadata_path):
            if (
                metadata.get(cfg.places_metadata_owner_attribute)
                != cfg.places_metadata_owner
            ):
                continue
            marker = metadata.find(cfg.places_hidden_element)
            if marker is None:
                ElementTree.SubElement(
                    metadata, cfg.places_hidden_element
                ).text = cfg.places_hidden_value
                changed = True
            elif marker.text != cfg.places_hidden_value:
                marker.text = cfg.places_hidden_value
                changed = True
    if not changed:
        return None
    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<!DOCTYPE {cfg.places_root_tag}>\n"
    )
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
    path = Path(cfg.home_dir) / cfg.user_places_file
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        _log("no user-places.xbel found, Places hiding applies after first login")
        return False
    try:
        content = _places_xbel_hidden(cfg, current, set(cfg.places_hidden))
    except ElementTree.ParseError as exc:
        _log(f"cannot parse {cfg.user_places_file}: {exc}, Places hiding skipped")
        return False
    if content is None:
        return False
    return _write_user_file(
        cfg,
        str(cfg.user_places_file),
        content,
        mode=cfg.default_file_mode,
        timeout=timeout,
        force=force,
    )


def _apply_user_dirs(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the configured XDG user directories; True when changed."""

    path = Path(cfg.home_dir) / cfg.user_config_dir / cfg.user_dirs_file
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    content = _user_dirs_merged(current, cfg.user_dirs)
    return _write_user_file(
        cfg,
        f"{cfg.user_config_dir}/{cfg.user_dirs_file}",
        content,
        mode=cfg.default_file_mode,
        timeout=timeout,
        force=force,
    )


def _apply_konsole_profile(
    cfg: KdeSettingsConfig,
    template_path: Path,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the Pyntara Konsole profile; True when changed.

    The template is rendered with the target home directory, so the profile
    points at the right Downloads directory on any target machine.
    """

    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError:
        _log("no konsole profile template, profile left as is")
        return False
    content = template.replace("{home_dir}", cfg.home_dir)
    return _write_user_file(
        cfg,
        str(cfg.konsole_profile_path),
        content,
        mode=cfg.default_file_mode,
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
    warnings: list[str] | None = None,
) -> bool:
    """Write the SDDM autologin and theme; True when any changed.

    The values go into the system files /etc/sddm.conf and
    /etc/sddm.conf.d/20-kubuntu.conf as the root process, so they apply to
    the login screen on every boot. Each value is guarded, so one failed
    write is reported and the remaining values still apply.
    """

    changed = False
    for key, value in (
        ("User", cfg.sddm_autologin_user),
        ("Session", cfg.sddm_autologin_session),
    ):
        try:
            changed |= _sync_system_value(
                str(cfg.sddm_conf_file),
                ("Autologin",),
                key,
                value,
                timeout=timeout,
                force=force,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot set the SDDM autologin {key}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    for key, value in (
        ("Current", cfg.sddm_theme),
        ("CursorSize", cfg.sddm_theme_cursor_size),
        ("CursorTheme", cfg.sddm_theme_cursor_theme),
        ("Font", cfg.sddm_theme_font),
    ):
        try:
            changed |= _sync_system_value(
                str(cfg.sddm_theme_conf_file),
                ("Theme",),
                key,
                value,
                timeout=timeout,
                force=force,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot set the SDDM theme {key}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    return changed


def _reload_kwin(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    env: dict[str, str] | None,
) -> str | None:
    """Reload the kwin configuration; error text or None.

    Runs after the Wayland input method changed so kwin re-reads kwinrc.
    A missing live session is not an error: the setting applies at login.
    """

    if env is None:
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
    env: dict[str, str] | None,
    system_python: str,
) -> str | None:
    """Apply the configured desktop count through the DBus API; error or None.

    KWin reads the desktop count from kwinrc only at session start, so a
    kwin reconfigure does not apply a changed Number. This function reads
    the target count from the kconfig records and creates or removes
    desktops through the VirtualDesktopManager DBus API to match it live.
    A missing live session is not an error: the count applies at the next
    login.
    """

    if env is None:
        return None
    target = None
    for record in cfg.kconfig:
        if (
            record.file == cfg.kwinrc_file_name
            and record.group == cfg.desktops_group
            and record.key == cfg.desktop_count_key
        ):
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
        for position in range(current, target):
            try:
                run_command(
                    _as_user_command(
                        cfg,
                        [
                            "qdbus6",
                            "org.kde.KWin",
                            "/VirtualDesktopManager",
                            "org.kde.KWin.VirtualDesktopManager.createDesktop",
                            str(position),
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
        ids_result = run_command(
            _as_user_command(
                cfg, [system_python, "-c", _DESKTOP_IDS_CLIENT]
            ),
            extra_env=env,
            timeout=timeout,
            capture=True,
        )
        ids = trim_whitespace(ids_result.stdout).splitlines()
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


def _run_settings_step(
    warnings: list[str],
    description: str,
    call: Callable[[], bool],
) -> bool:
    """Run one independent settings step; warn and continue on a failure.

    A settings step that fails through an external tool error or an
    environment error must not stop the remaining independent steps: the
    value is left for the next run and the failure is reported as a
    warning, so the whole task never dies because of one bad setting.
    """

    try:
        return call()
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ) as exc:
        warning = f"cannot {description}: {exc}"
        _log(warning)
        warnings.append(warning)
        return False


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
    day and night switch instead, so a run never fights the switch. Each
    settings step runs independently: a step that fails through an external
    tool error or an environment error is reported as a warning and the
    remaining independent steps still run, because one bad setting must
    not stop the rest. Missing packages are attempted one by one; when a
    package cannot be installed the task reports it in its warnings and
    stops its own settings, because its mechanism is incomplete.
    """

    cfg = ctx.config.kde_settings
    timeout = ctx.config.engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    packages_failed = False

    for package in cfg.packages:
        if package_is_installed(package, timeout):
            continue
        _log(f"installing {package}")
        ok, error = install_package_once(package, timeout)
        if not ok:
            packages_failed = True
            warning = f"cannot install {package}: {error}"
            _log(warning)
            warnings.append(warning)
        else:
            changed = True

    if packages_failed:
        return TaskResult(
            success=True,
            changed=changed,
            message="KDE appearance and input settings not configured",
            warnings=tuple(warnings),
        )

    def step(description: str, call: Callable[[], bool]) -> bool:
        return _run_settings_step(warnings, description, call)

    try:
        run_command(
            _as_user_command(
                cfg, ["mkdir", "-p", str(Path(cfg.home_dir) / cfg.user_config_dir)]
            ),
            extra_env=_home_env(cfg),
            timeout=timeout,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
    ) as exc:
        warnings.append(f"cannot create the user config directory: {exc}")
    apply_env = _apply_env(cfg, ctx.config.engine)
    if apply_env is None:
        _log("no desktop session found, settings apply after login")

    settings_changed = False
    virtual_keyboard_changed = False
    kwin_scripts_changed = False
    if cfg.automatic_look_and_feel:
        settings_changed |= step(
            "enable the automatic theme switch",
            lambda: _apply_automatic_look_and_feel(
                cfg,
                timeout=timeout,
                force=force,
                env=apply_env,
                warnings=warnings,
            ),
        )
    else:
        settings_changed |= step(
            "apply the global theme",
            lambda: _apply_look_and_feel(
                cfg, env=apply_env, timeout=timeout, force=force
            ),
        )
        settings_changed |= step(
            "apply the color scheme",
            lambda: _apply_color_scheme(
                cfg, env=apply_env, timeout=timeout, force=force
            ),
        )
    settings_changed |= step(
        "set the NumLock state",
        lambda: _apply_numlock(cfg, timeout=timeout, force=force),
    )
    settings_changed |= step(
        "set the touchpad preferences",
        lambda: _apply_touchpad(
            cfg, timeout=timeout, force=force, warnings=warnings
        ),
    )
    virtual_keyboard_changed = step(
        "set the Wayland virtual keyboard",
        lambda: _apply_virtual_keyboard(
            cfg,
            timeout=timeout,
            force=force,
            env=apply_env,
            warnings=warnings,
        ),
    )
    settings_changed |= virtual_keyboard_changed
    settings_changed |= step(
        "apply the configured kconfig values",
        lambda: _apply_kconfig_records(
            cfg, timeout=timeout, force=force, env=apply_env, warnings=warnings
        ),
    )
    settings_changed |= step(
        "write the theme cursor overrides",
        lambda: _apply_theme_cursor_overrides(
            cfg, timeout=timeout, force=force, warnings=warnings
        ),
    )
    settings_changed |= step(
        "apply the cursor theme",
        lambda: _apply_cursor_theme(
            cfg, env=apply_env, timeout=timeout, force=force
        ),
    )
    settings_changed |= step(
        "clear the conflicting shortcuts",
        lambda: _clear_shortcut_conflicts(
            cfg, timeout=timeout, warnings=warnings
        ),
    )
    kwin_scripts_changed = step(
        "install and enable the kwin scripts",
        lambda: _apply_kwin_scripts(
            cfg,
            task_data_dir(ctx.repo_root, ctx.task_name) / "kwin",
            timeout=timeout,
            force=force,
            env=apply_env,
            warnings=warnings,
        ),
    )
    settings_changed |= kwin_scripts_changed
    settings_changed |= step(
        "free the kwin script hotkeys",
        lambda: _free_script_hotkeys(
            cfg,
            env=apply_env,
            timeout=timeout,
            system_python=ctx.config.engine.system_python,
            warnings=warnings,
        ),
    )
    settings_changed |= step(
        "write the XDG user directories",
        lambda: _apply_user_dirs(cfg, timeout=timeout, force=force),
    )
    settings_changed |= step(
        "write the Konsole profile",
        lambda: _apply_konsole_profile(
            cfg,
            task_data_dir(ctx.repo_root, ctx.task_name) / "Pyntara.profile",
            timeout=timeout,
            force=force,
        ),
    )
    settings_changed |= step(
        "hide the configured Dolphin places",
        lambda: _apply_places_hidden(cfg, timeout=timeout, force=force),
    )
    settings_changed |= step(
        "write the SDDM settings",
        lambda: _apply_sddm(
            cfg, timeout=timeout, force=force, warnings=warnings
        ),
    )
    changed |= settings_changed

    kwinrc_changed = any(
        record.file == cfg.kwinrc_file_name for record in cfg.kconfig
    ) and settings_changed
    if virtual_keyboard_changed or kwinrc_changed or kwin_scripts_changed:
        reload_error = _reload_kwin(cfg, timeout=timeout, env=apply_env)
        if reload_error is not None:
            _log(reload_error)
            warnings.append(reload_error)

    desktop_error = _apply_desktop_count_live(
        cfg,
        timeout=timeout,
        env=apply_env,
        system_python=ctx.config.engine.system_python,
    )
    if desktop_error is not None:
        _log(desktop_error)
        warnings.append(desktop_error)

    if warnings:
        return TaskResult(
            success=True,
            changed=changed,
            message="KDE appearance and input settings configured with warnings",
            warnings=tuple(warnings),
        )
    if not changed:
        return TaskResult(success=True, changed=False, message="already configured")
    return TaskResult(
        success=True,
        changed=True,
        message="KDE appearance and input settings configured",
    )
