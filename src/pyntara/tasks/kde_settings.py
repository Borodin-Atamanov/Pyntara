"""Task kde_settings: apply the KDE appearance and input settings.

The task applies the configured dark appearance as the target user: the
color scheme that turns every Qt and KDE window dark and the global theme
that covers the whole desktop (panel, widgets, window decorations, icons).
Both values are applied with the plasma-apply tools through runuser, so
the config files stay owned by that user. The task also applies the input
and keyboard settings as KConfig values with kwriteconfig6: the NumLock
state on startup, the touchpad click method (to every touchpad found) and
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
first. The task also installs the KWin scripts of the section and owns
their keyboard combinations: the combination is freed from whatever
action holds it, given to the script action in the running KGlobalAccel
daemon and read back, so each combination works whatever owned it before
the run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from string import Template
from typing import TypedDict
from xml.etree import ElementTree

from pyntara.config import EngineConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_package_once,
    kglobalaccel_names,
    package_is_installed,
    run_command,
    session_environment,
    substituted_command,
    task_data_dir,
    trim_whitespace,
)
from pyntara.values import common as common_values
from pyntara.values import kde_settings as values

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
# The KWin scripts the task installs and enables live as directories under
# task_data/kde_settings/kwin in the clone, one directory per script; their
# names and the files each one carries come from the config, and the root
# comes from the context.
# The client that prints the id of every virtual desktop, one per line, in
# position order, ships under task_data/kde_settings/list_desktop_ids.py;
# its name comes from the config. The desktop list is a DBus property of
# structs (position, id, name), qdbus6 cannot render that type, so the task
# reads the ids through python3-dbus.


def _as_user_command(command: list[str]) -> list[str]:
    """Prefix a command with the configured wrapper of the target user.

    The wrapper is a value of the section, so a machine whose desktop user
    is reached another way is a values change.
    """

    return [
        *substituted_command(
            values.RUNUSER_COMMAND, {"username": common_values.DESKTOP_USERNAME}
        ),
        *command,
    ]


def _home_env() -> dict[str, str]:
    """Environment that points the KDE tools at the target user home."""

    return {"HOME": common_values.DESKTOP_HOME_DIR}


def _kconfig_command(
    base_command: tuple[str, ...],
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
) -> list[str]:
    """One KConfig call: the configured base, the groups and the key.

    The base call carries the file name and every selector is a value of
    the section, so another KConfig version or another tool is a values
    change. The reader, the writer and the delete share this builder, so
    the three calls can never drift apart.
    """

    command = substituted_command(base_command, {"file_name": file_name})
    for segment in group_segments:
        command.extend(
            substituted_command(values.CONFIG_GROUP_FLAG, {"group": segment})
        )
    command.extend(substituted_command(values.CONFIG_KEY_FLAG, {"key": key}))
    return command


def _kreadconfig(
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    timeout: float,
) -> str:
    """Current value of one KConfig key, or an empty string when unset."""

    command = _kconfig_command(
        values.KREADCONFIG_COMMAND, file_name, group_segments, key
    )
    result = run_command(
        _as_user_command(command),
        extra_env=_home_env(),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return trim_whitespace(result.stdout)


def _notify_flag(
    file_name: str, env: dict[str, str] | None
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
    if file_name not in (values.KWINRC_FILE_NAME, values.KDEGLOBALS_FILE_NAME):
        return []
    return list(values.CONFIG_NOTIFY_FLAG)


def _kwriteconfig(
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

    command = _kconfig_command(
        values.KWRITECONFIG_COMMAND, file_name, group_segments, key
    )
    if bool_value:
        command.extend(values.CONFIG_BOOL_TYPE_FLAG)
    command.append(value)
    command.extend(_notify_flag(file_name, env))
    write_env = env if env is not None else _home_env()
    run_command(
        _as_user_command(command),
        extra_env=write_env,
        timeout=timeout,
    )


def _delete_kconfig_key(
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> None:
    """Delete one KConfig key with kwriteconfig6 as the target user."""

    command = _kconfig_command(
        values.KWRITECONFIG_COMMAND, file_name, group_segments, key
    )
    command.extend(values.CONFIG_DELETE_FLAG)
    command.extend(_notify_flag(file_name, env))
    write_env = env if env is not None else _home_env()
    run_command(
        _as_user_command(command),
        extra_env=write_env,
        timeout=timeout,
    )


def _sync_config_value(
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

    current = _kreadconfig(file_name, group_segments, key, timeout)
    if not force and current == target:
        return False
    _kwriteconfig(
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


def _apply_env(engine: EngineConfig) -> dict[str, str] | None:
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
    env = _home_env()
    env.update(session)
    return env


def _appearance_command(value_name: str) -> list[str]:
    """The configured plasma-apply call with the value it applies.

    The three appearance tools, the flags they take and the value they
    apply all come from the values of the section, so another tool or
    another flag is a values change. value_name selects which of the three
    calls is rendered and names the placeholder the value is substituted
    for.
    """

    base, value = {
        "look_and_feel": (values.APPLY_LOOK_AND_FEEL_COMMAND, values.LOOK_AND_FEEL),
        "color_scheme": (values.APPLY_COLOR_SCHEME_COMMAND, values.COLOR_SCHEME),
        "cursor_theme": (values.APPLY_CURSOR_THEME_COMMAND, values.CURSOR_THEME),
    }[value_name]
    return substituted_command(base, {value_name: value})


def _run_appearance_tool_best_effort(
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
        run_command(_as_user_command(command), extra_env=env, timeout=timeout)
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
        values.KDEGLOBALS_FILE_NAME,
        values.KDE_GROUP,
        values.LOOK_AND_FEEL_PACKAGE_KEY,
        timeout,
    )
    if not force and current == values.LOOK_AND_FEEL:
        return False
    _sync_config_value(
        values.KDEGLOBALS_FILE_NAME,
        values.KDE_GROUP,
        values.LOOK_AND_FEEL_PACKAGE_KEY,
        values.LOOK_AND_FEEL,
        timeout=timeout,
        force=force,
        bool_value=False,
        env=env,
    )
    _run_appearance_tool_best_effort(
        command=_appearance_command("look_and_feel"),
        applied_message=f"applied global theme: {values.LOOK_AND_FEEL}",
        timeout=timeout,
        env=env,
    )
    return True


def _apply_color_scheme(
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
        values.KDEGLOBALS_FILE_NAME,
        values.GENERAL_GROUP,
        values.COLOR_SCHEME_KEY,
        timeout,
    )
    if not force and current == values.COLOR_SCHEME:
        return False
    _sync_config_value(
        values.KDEGLOBALS_FILE_NAME,
        values.GENERAL_GROUP,
        values.COLOR_SCHEME_KEY,
        values.COLOR_SCHEME,
        timeout=timeout,
        force=force,
        bool_value=False,
        env=env,
    )
    _run_appearance_tool_best_effort(
        command=_appearance_command("color_scheme"),
        applied_message=f"applied color scheme: {values.COLOR_SCHEME}",
        timeout=timeout,
        env=env,
    )
    return True


def _apply_automatic_look_and_feel(
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

    if not values.AUTOMATIC_LOOK_AND_FEEL:
        return False
    changed = _guard_write(
        warnings,
        "enable the automatic theme switch",
        lambda: _sync_config_value(
            values.KDEGLOBALS_FILE_NAME,
            values.KDE_GROUP,
            values.AUTOMATIC_LOOK_AND_FEEL_KEY,
            common_values.KCONFIG_TRUE_VALUE,
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
            values.KDEGLOBALS_FILE_NAME,
            values.KDE_GROUP,
            values.AUTOMATIC_LOOK_AND_FEEL_IDLE_INTERVAL_KEY,
            values.AUTOMATIC_THEME_SWITCH_IDLE_INTERVAL,
            timeout=timeout,
            force=force,
            bool_value=False,
            env=env,
        ),
    )
    return changed


def _apply_numlock(
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the NumLock startup state; True when changed."""

    return _sync_config_value(
        values.KCMINPUTRC_FILE_NAME,
        values.KEYBOARD_GROUP,
        values.NUMLOCK_KEY,
        values.NUMLOCK_VALUES[values.NUMLOCK_ON_BOOT],
        timeout=timeout,
        force=force,
        bool_value=False,
    )


def _touchpad_groups(
    text: str, group_root: str, device_word: str
) -> list[tuple[str, ...]]:
    """The touchpad device groups of kcminputrc.

    A group names one libinput device as [root][...][device name]. The
    numeric libinput ids in a group are machine-specific, so the task
    matches devices by name; a device name that ends with the configured
    word identifies a touchpad on any target hardware. The root group and
    the word are values of the section, so another KDE release is answered
    in the values and not in the code.
    """

    groups: list[tuple[str, ...]] = []
    current: tuple[str, ...] = ()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = tuple(part for part in line[1:-1].split("][") if part)
            if (
                len(current) >= 4
                and current[0] == group_root
                and current[-1].endswith(device_word)
            ):
                groups.append(current)
    return groups


def _apply_touchpad(
    *,
    timeout: float,
    force: bool,
    warnings: list[str] | None = None,
) -> bool:
    """Write the touchpad click method to every touchpad found.

    The touchpad group ids are machine-specific and the target device is
    unknown, so the task applies the click method to every libinput group
    whose device name ends with Touchpad; no touchpad is not an error. A
    group that fails to write is reported and the remaining groups still
    apply.
    """

    kcminputrc = (
        Path(common_values.DESKTOP_HOME_DIR)
        / values.USER_CONFIG_DIR
        / values.KCMINPUTRC_FILE_NAME
    )
    try:
        groups = _touchpad_groups(
            kcminputrc.read_text(encoding="utf-8"),
            values.TOUCHPAD_GROUP_ROOT,
            values.TOUCHPAD_DEVICE_WORD,
        )
    except OSError:
        _log(
            f"no {values.KCMINPUTRC_FILE_NAME} found, touchpad settings left as is"
        )
        return False
    if not groups:
        _log("no touchpad found, touchpad settings left as is")
        return False
    changed = False
    for group in groups:
        try:
            changed |= _sync_config_value(
                values.KCMINPUTRC_FILE_NAME,
                group,
                values.CLICK_METHOD_KEY,
                values.CLICK_METHOD_VALUES[values.TOUCHPAD_CLICK_METHOD],
                timeout=timeout,
                force=force,
                bool_value=False,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot set the touchpad click method for {group[-1]}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    return changed


def _apply_virtual_keyboard(
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
    if values.VIRTUAL_KEYBOARD_ENABLED:
        changed |= _guard_write(
            warnings,
            "set the Wayland input method",
            lambda: _sync_config_value(
                values.KWINRC_FILE_NAME,
                values.WAYLAND_GROUP,
                values.INPUT_METHOD_KEY,
                values.VIRTUAL_KEYBOARD_INPUT_METHOD,
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
                values.PLASMA_KEYBOARD_FILE_NAME,
                values.VIRTUAL_KEYBOARD_GROUP,
                values.INPUT_METHOD_LOCALES_KEY,
                ",".join(values.VIRTUAL_KEYBOARD_LOCALES),
                timeout=timeout,
                force=force,
                bool_value=False,
                env=env,
            ),
        )
    else:
        current = _kreadconfig(
            values.KWINRC_FILE_NAME,
            values.WAYLAND_GROUP,
            values.INPUT_METHOD_KEY,
            timeout,
        )

        def remove_input_method() -> bool:
            _delete_kconfig_key(
                values.KWINRC_FILE_NAME,
                values.WAYLAND_GROUP,
                values.INPUT_METHOD_KEY,
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
        values.KCMINPUTRC_FILE_NAME,
        values.MOUSE_GROUP,
        values.CURSOR_THEME_KEY,
        timeout,
    )
    if not force and current == values.CURSOR_THEME:
        return False
    _sync_config_value(
        values.KCMINPUTRC_FILE_NAME,
        values.MOUSE_GROUP,
        values.CURSOR_THEME_KEY,
        values.CURSOR_THEME,
        timeout=timeout,
        force=force,
        bool_value=False,
        env=env,
    )
    _run_appearance_tool_best_effort(
        command=_appearance_command("cursor_theme"),
        applied_message=f"applied cursor theme: {values.CURSOR_THEME}",
        timeout=timeout,
        env=env,
    )
    return True


def _apply_theme_cursor_overrides(
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
        (values.LOOK_AND_FEEL, values.CURSOR_THEME),
        (values.LOOK_AND_FEEL_LIGHT, values.CURSOR_THEME_LIGHT),
    ):
        try:
            source = values.SYSTEM_LOOK_AND_FEEL_DIR / look_and_feel
            if not source.is_dir():
                _log(f"no system theme {look_and_feel}, cursor override skipped")
                continue
            target = (
                Path(common_values.DESKTOP_HOME_DIR)
                / values.USER_LOOK_AND_FEEL_DIR
                / look_and_feel
            )
            if not target.is_dir():
                shutil.copytree(source, target)
                run_command(
                    substituted_command(
                        values.CHOWN_RECURSIVE_COMMAND,
                        {
                            "owner": (
                                f"{common_values.DESKTOP_USERNAME}:"
                                f"{common_values.DESKTOP_USERNAME}"
                            ),
                            "path": str(target),
                        },
                    ),
                    timeout=timeout,
                )
                _log(
                    f"copied theme {look_and_feel} into the user look and feel "
                    "directory"
                )
            changed |= _sync_config_value(
                str(target / values.THEME_DEFAULTS_DIR),
                (values.KCMINPUTRC_FILE_NAME, *values.MOUSE_GROUP),
                values.CURSOR_THEME_KEY,
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
    for record in values.KCONFIG_RECORDS:
        try:
            if _is_shortcut_record(record):
                # The running daemon owns the shortcut state and writes
                # the shortcut file from its memory, so these records are
                # applied to the daemon in their own step and never
                # compared as file text.
                continue
            if record.delete:
                current = _kreadconfig(
                    record.file, record.group, record.key, timeout
                )
                if not force and not current:
                    continue
                _delete_kconfig_key(
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
                record.file,
                record.group,
                record.key,
                record.value,
                timeout=timeout,
                force=force,
                bool_value=record.type == values.KCONFIG_BOOL_TYPE,
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


def _is_shortcut_record(record: values.KconfigRecord) -> bool:
    """True when a record names the keyboard combination of one action.

    The shortcut file carries one record per action of a component, in
    the form key,defaults,friendly name, which is what the comma test
    recognises. A delete record is a plain KConfig removal and stays with
    the other records of the file.
    """

    return (
        record.file == common_values.SHORTCUTS_FILE_NAME
        and not record.delete
        and "," in record.value
    )


def _shortcut_record_changes(
    cfg: KdeSettingsConfig,
) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    """The configured combinations, by component, action and combination.

    A shortcut record names one action and the combination it must own.
    Only the first comma field of its value is read: the second field
    holds the combination the action ships with and the third its
    friendly name, both of which the running daemon reports and writes
    itself, so a record copied from the shortcut file carries the default
    combination in the second field, and reading that field as a
    configured combination would take a key the action must not own. The
    absent word and an empty field mean no combination at all. The group
    segment of the record is the unique component name the running daemon
    knows and the key is the unique action name inside that component;
    the component friendly name of the change is that same name, because a
    record names no other.
    """

    changes: list[tuple[str, str, str, tuple[str, ...]]] = []
    for record in cfg.kconfig:
        if not _is_shortcut_record(cfg, record):
            continue
        if not record.group:
            _log(
                f"no component in the record of {record.key},"
                " the shortcut is left as is"
            )
            continue
        keys: list[str] = []
        text = trim_whitespace(record.value.split(",", 1)[0])
        if text and text != cfg.shortcut_absent_value:
            keys.append(text)
        component_unique = record.group[0]
        changes.append(
            (component_unique, component_unique, record.key, tuple(keys))
        )
    return tuple(changes)


def _shortcut_apply_request(
    changes: tuple[tuple[str, str, str, tuple[str, ...]], ...],
) -> str:
    """The JSON request of the shared client: one change per combination."""

    return json.dumps(
        {
            "changes": [
                {
                    "component_unique": component_unique,
                    "component_friendly": component_friendly,
                    "action": action,
                    "keys": list(keys),
                }
                for component_unique, component_friendly, action, keys in changes
            ]
        }
    )


class _ShortcutReport(TypedDict):
    """One change as the shared client reports it.

    requested holds the combined codes the client read from the configured
    combinations, before and after the codes the action held around the
    call, unsupported the combinations Qt could not read, and missing
    marks an action the daemon does not know.
    """

    action: str
    requested: list[int]
    before: list[int]
    after: list[int]
    unsupported: list[str]
    missing: bool


def _report_is_confirmed(report: _ShortcutReport) -> bool:
    """True when the daemon holds exactly the requested combinations.

    An action the daemon does not know and a combination the client cannot
    read are never confirmed, whatever the daemon reports, because the
    requested state was not reached.
    """

    return (
        not report.get("missing")
        and not report.get("unsupported")
        and report.get("after") == report.get("requested")
    )


def _a_repeat_can_confirm(reports: list[_ShortcutReport]) -> bool:
    """True when asking again can still reach the configured state.

    Only a plain difference can be decided differently by a second attempt,
    because the daemon resolves a conflict against the first one; an action
    it does not know and a combination the client cannot read stay
    unreachable, so the attempts stop instead of waiting for them.
    """

    return any(
        not report.get("missing")
        and not report.get("unsupported")
        and report.get("after") != report.get("requested")
        for report in reports
    )


def _shortcut_changes(
    cfg: KdeSettingsConfig,
) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    """Every combination the task must give to an action.

    The configured records come first, then the combination of each KWin
    script action, so one call to the shared client carries the whole
    intended state of the keyboard combinations: a record of the shortcut
    file and a combination a script claims are two changes of the same kind
    and are reported the same way.
    """

    changes = list(_shortcut_record_changes(cfg))
    changes.extend(
        (
            cfg.kwin_component_unique,
            cfg.kwin_component_friendly,
            action,
            (hotkey,),
        )
        for action, hotkey in _script_hotkey_pairs(cfg)
    )
    return tuple(changes)


def _shortcut_record_value(
    cfg: KdeSettingsConfig, current: str, keys: tuple[str, ...], action: str
) -> str:
    """The shortcut file value of one action: its key, then what follows.

    The first field is the combination the action owns; the fields after it
    are the default combinations and the friendly name, which the running
    daemon owns. They are kept exactly as the file has them, so a write
    never loses them. An action the file does not know yet gets the absent
    word as its default combination.
    """

    first = ",".join(keys) if keys else cfg.shortcut_absent_value
    if not current:
        return f"{first},{cfg.shortcut_absent_value},{action}"
    _head, separator, rest = current.partition(",")
    if not separator:
        rest = current
    return f"{first},{rest}"


def _write_shortcut_records_to_file(
    cfg: KdeSettingsConfig,
    changes: tuple[tuple[str, str, str, tuple[str, ...]], ...],
    *,
    timeout: float,
    warnings: list[str] | None = None,
) -> None:
    """Write combinations into the shortcut file for the next login.

    The running daemon owns the combinations and writes the file from its
    memory, so a value written here survives only until the next change
    made through the daemon. The write is therefore a fallback for a
    combination the daemon did not confirm: it gives the next login the
    configured key even when the live call could not run. Only the first
    field of a record is written and the rest of it is kept, so nothing the
    desktop needs is lost. Every other record that holds one of these keys
    gets the absent word in its first field, so the next login does not hand
    a key to an action that was not running during this run. A record that
    cannot be read or written is reported and the remaining records still
    write.
    """

    owned_keys = {
        key
        for _component_unique, _component_friendly, _action, keys in changes
        for key in keys
    }
    own_records = {
        (component_unique, action)
        for component_unique, _component_friendly, action, _keys in changes
    }
    for component_unique, _component_friendly, action, keys in changes:
        record_group = (component_unique,)
        try:
            current = _kreadconfig(
                cfg, cfg.global_shortcuts_file_name, record_group, action, timeout
            )
            _kwriteconfig(
                cfg,
                cfg.global_shortcuts_file_name,
                record_group,
                action,
                _shortcut_record_value(cfg, current, keys, action),
                timeout=timeout,
                bool_value=False,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = (
                f"cannot write the shortcut record {action} of"
                f" {component_unique}: {exc}"
            )
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
    if not owned_keys:
        return
    path = Path(cfg.home_dir) / cfg.user_config_dir / cfg.global_shortcuts_file_name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    section: tuple[str, ...] = ()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = tuple(part for part in stripped[1:-1].split("][") if part)
            continue
        key, separator, value = stripped.partition("=")
        if not separator or "," not in value:
            continue
        if ((section[0] if section else ""), key) in own_records:
            continue
        first, _comma, rest = value.partition(",")
        if trim_whitespace(first) not in owned_keys:
            continue
        try:
            _kwriteconfig(
                cfg,
                cfg.global_shortcuts_file_name,
                section,
                key,
                f"{cfg.shortcut_absent_value},{rest}",
                timeout=timeout,
                bool_value=False,
            )
            _log(f"cleared the conflicting shortcut {key} of {section}: {value}")
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot clear the conflicting shortcut {key}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)


def _apply_shortcuts_live(
    cfg: KdeSettingsConfig,
    *,
    client_path: Path,
    timeout: float,
    env: dict[str, str] | None,
    system_python: str,
    kglobalaccel_names: dict[str, str],
    warnings: list[str] | None = None,
) -> bool:
    """Give every configured combination to its action; True when changed.

    The running daemon holds the combinations in memory and writes the
    shortcut file from that memory, so the whole intended state goes to the
    daemon in one call: the shared client frees every named combination
    from whatever action holds it and gives it to the configured action,
    and reports per change the combinations the action held before and
    after. A state that is still not the configured one is asked for again
    while a repeat can still change it, because the daemon can decide a
    conflict against the first attempt; a state a repeat cannot reach, an
    action the daemon does not know, and a combination the client cannot
    read, are reported at once and never guessed at. Every combination the
    daemon does not hold after the last attempt is written into the
    shortcut file, so the next login gets it even though the running
    session did not.
    """

    changes = _shortcut_changes(cfg)
    if not changes:
        return False

    def report_and_write_for_next_login(
        remaining: tuple[tuple[str, str, str, tuple[str, ...]], ...],
        warning: str,
    ) -> None:
        _log(warning)
        if warnings is not None:
            warnings.append(warning)
        _write_shortcut_records_to_file(
            cfg, remaining, timeout=timeout, warnings=warnings
        )

    if env is None:
        _log("no desktop session, the shortcuts apply at the next login")
        _write_shortcut_records_to_file(
            cfg, changes, timeout=timeout, warnings=warnings
        )
        return False
    try:
        client_text = Template(client_path.read_text(encoding="utf-8")).substitute(
            **kglobalaccel_names
        )
    except OSError as exc:
        report_and_write_for_next_login(
            changes, f"cannot read the shortcut client {client_path}: {exc}"
        )
        return False
    command = _as_user_command(
        cfg,
        [
            *substituted_command(
                cfg.python_script_command, {"python": system_python}
            ),
            client_text,
            _shortcut_apply_request(changes),
        ],
    )
    reports: list[_ShortcutReport] = []
    for attempt in range(1, cfg.shortcut_apply_attempts + 1):
        if attempt > 1:
            time.sleep(cfg.shortcut_apply_retry_delay_seconds)
        try:
            result = run_command(
                command, extra_env=env, timeout=timeout, capture=True
            )
        except subprocess.CalledProcessError as exc:
            detail = trim_whitespace(exc.stderr or "")
            suffix = f": {detail}" if detail else ""
            report_and_write_for_next_login(
                changes, f"cannot apply the configured shortcuts: {exc}{suffix}"
            )
            return False
        except subprocess.TimeoutExpired as exc:
            report_and_write_for_next_login(
                changes, f"cannot apply the configured shortcuts: {exc}"
            )
            return False
        try:
            reply = json.loads(result.stdout)
        except json.JSONDecodeError:
            report_and_write_for_next_login(
                changes, f"cannot read the kglobalaccel reply: {result.stdout}"
            )
            return False
        reports = list(reply.get("results") or [])
        if len(reports) != len(changes):
            report_and_write_for_next_login(
                changes,
                f"the client reported {len(reports)} of {len(changes)}"
                " configured shortcuts",
            )
            return False
        if attempt == 1:
            for (_component, _friendly, action, _keys), report in zip(
                changes, reports
            ):
                if report.get("missing"):
                    _log(
                        f"the daemon does not know the action {action},"
                        " its shortcut is written for the next login"
                    )
                for text in report.get("unsupported"):
                    _log(
                        f"the client cannot read {text} of {action},"
                        " its shortcut is written for the next login"
                    )
        if all(_report_is_confirmed(report) for report in reports):
            break
        if not _a_repeat_can_confirm(reports):
            break
    changed = any(
        report.get("before") != report.get("after") for report in reports
    )
    unconfirmed = tuple(
        change
        for change, report in zip(changes, reports)
        if not _report_is_confirmed(report)
    )
    if not unconfirmed:
        _log(f"applied {len(changes)} configured shortcuts in the running daemon")
        return changed
    # The combinations the daemon still refuses are reported, and the work
    # done on the remaining actions is reported as well: a difference is
    # often partial, and hiding the part that took would make the next run
    # start from a wrong idea of the machine.
    difference = [
        (action, report.get("after"), report.get("requested"))
        for (_component, _friendly, action, _keys), report in zip(changes, reports)
        if not _report_is_confirmed(report)
    ]
    report_and_write_for_next_login(
        unconfirmed,
        "the daemon does not hold the configured shortcuts:"
        f" {difference}, they are written into the shortcut file for the"
        " next login",
    )
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
        _as_user_command(
            cfg,
            substituted_command(
                cfg.mkdir_command, {"path": str(target.parent)}
            ),
        ),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )
    # The user mkdir above owns the directory; this direct creation is a
    # no-op when it succeeded and a fallback for a read-only fixture.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_command(
        substituted_command(
            cfg.chown_command,
            {
                "owner": f"{cfg.username}:{cfg.username}",
                "path": str(target),
            },
        ),
        timeout=timeout,
    )
    run_command(
        substituted_command(
            cfg.chmod_command,
            {"file_mode": f"{mode:04o}", "path": str(target)},
        ),
        timeout=timeout,
    )
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


def _script_hotkey_pairs(
    cfg: KdeSettingsConfig,
) -> tuple[tuple[str, str], ...]:
    """The script hotkeys as their action and combination.

    The configured action list and hotkey list describe one hotkey per
    position; the shared client turns a combination into the combined Qt
    key code the daemon takes, so the two lists are all the task needs.
    """

    return tuple(zip(cfg.kwin_script_actions or (), cfg.kwin_script_hotkeys or ()))


def _write_script_hotkey_records(
    cfg: KdeSettingsConfig,
    *,
    timeout: float,
    force: bool,
    warnings: list[str] | None = None,
) -> bool:
    """Write the claimed combination into the record of each action.

    A record whose key slot is empty, the state the daemon leaves behind
    when it refuses a registration, wins over the combination the script
    registers, so the hotkey would stay dead at every login. Every
    existing record of a script action is therefore rewritten as
    combination,none,description, the shape a granted hotkey has; an
    action without a record is left alone, because the script writes its
    own record when it registers at login. The rewrite runs as the target
    user and a record that cannot be read or written is reported and the
    remaining records still write.
    """

    changed = False
    for action, hotkey in _script_hotkey_pairs(cfg):
        group = (cfg.kwin_component_unique,)
        try:
            current = _kreadconfig(
                cfg, cfg.global_shortcuts_file_name, group, action, timeout
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot read the script hotkey record {action}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
            continue
        if not current or "," not in current:
            continue
        fields = current.split(",")
        description = fields[2] if len(fields) > 2 and fields[2] else action
        target = f"{hotkey},none,{description}"
        if not force and current == target:
            continue
        try:
            _kwriteconfig(
                cfg,
                cfg.global_shortcuts_file_name,
                group,
                action,
                target,
                timeout=timeout,
                bool_value=False,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            warning = f"cannot write the script hotkey record {action}: {exc}"
            _log(warning)
            if warnings is not None:
                warnings.append(warning)
            continue
        _log(f"wrote the script hotkey record {action}: {target}")
        changed = True
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
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    timeout: float,
) -> str:
    """Current value of one system KConfig key, read as the root process."""

    command = _kconfig_command(
        cfg, cfg.kreadconfig_command, file_name, group_segments, key
    )
    result = run_command(command, check=False, capture=True, timeout=timeout)
    return trim_whitespace(result.stdout)


def _system_kwriteconfig(
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    value: str,
    *,
    timeout: float,
) -> None:
    """Write one system KConfig key as the root process."""

    command = _kconfig_command(
        cfg, cfg.kwriteconfig_command, file_name, group_segments, key
    )
    command.append(value)
    run_command(command, timeout=timeout)


def _sync_system_value(
    cfg: KdeSettingsConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    target: str,
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write a system KConfig key when it differs; True when written."""

    current = _system_kreadconfig(cfg, file_name, group_segments, key, timeout)
    if not force and current == target:
        return False
    _system_kwriteconfig(
        cfg, file_name, group_segments, key, target, timeout=timeout
    )
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
                cfg,
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
                cfg,
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


def _desktop_dbus_names(cfg: KdeSettingsConfig) -> dict[str, str]:
    """The DBus vocabulary of the KWin desktop interface, by placeholder.

    The commands of the section and the desktop list client of task_data/
    name the same interface and the same properties, so all of them take
    it from these values and no name of the desktop interface stands in
    code.
    """

    return {
        "kwin_bus_name": cfg.kwin_bus_name,
        "virtual_desktop_manager_object_path": (
            cfg.virtual_desktop_manager_object_path
        ),
        "virtual_desktop_manager_interface_name": (
            cfg.virtual_desktop_manager_interface_name
        ),
        "virtual_desktops_property_name": cfg.virtual_desktops_property_name,
        "virtual_desktop_count_property_name": (
            cfg.virtual_desktop_count_property_name
        ),
        "dbus_properties_interface_name": cfg.dbus_properties_interface_name,
    }


def _desktop_list_client_text(cfg: KdeSettingsConfig, script_path: Path) -> str:
    """The desktop list client with the DBus names of the section filled in."""

    template = Template(script_path.read_text(encoding="utf-8"))
    return template.substitute(**_desktop_dbus_names(cfg))


def _apply_desktop_count_live(
    cfg: KdeSettingsConfig,
    *,
    script_path: Path,
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
                substituted_command(
                    cfg.kwin_desktop_count_command, _desktop_dbus_names(cfg)
                ),
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
                        substituted_command(
                            cfg.kwin_desktop_create_command,
                            {
                                **_desktop_dbus_names(cfg),
                                "position": str(position),
                                "desktop_name": "",
                            },
                        ),
                    ),
                    extra_env=env,
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                return f"cannot create desktop: {exc}"
        _log(f"created {target - current} desktops, live count now {target}")
    else:
        try:
            ids_client = _desktop_list_client_text(cfg, script_path)
        except OSError as exc:
            return f"cannot read the desktop list client {script_path}: {exc}"
        ids_result = run_command(
            _as_user_command(
                cfg,
                [
                    *substituted_command(
                        cfg.python_script_command, {"python": system_python}
                    ),
                    ids_client,
                ],
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
                        substituted_command(
                            cfg.kwin_desktop_remove_command,
                            {
                                **_desktop_dbus_names(cfg),
                                "desktop_id": desktop_id,
                            },
                        ),
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
    configured scheme wins, then the NumLock state, the touchpad click
    method, the Wayland virtual keyboard, the configured kconfig
    values, the theme cursor overrides that let the day and night switch
    apply the configured cursors, and the cursor theme last, so it wins
    over the theme default the switch writes. When automatic_look_and_feel
    is set, the theme is not applied directly: the task enables the native
    day and night switch instead, so a run never fights the switch. The
    KWin scripts of the section are installed and enabled first, and the
    whole intended state of the keyboard combinations is then applied to
    the running daemon in one call, which owns that state and writes the
    shortcut file itself: the configured records and the combinations the
    scripts claim are taken from whatever action holds them and given to
    the configured actions, a state the daemon still refuses is asked for
    again while a repeat can still change it, and every combination the
    daemon does not hold is written into the shortcut file for the next
    login, so a key works whatever owned it before the run. Each
    settings step runs independently: a step that fails through an external
    tool error or an environment error is reported as a warning and the
    remaining independent steps still run, because one bad setting must
    not stop the rest. Missing packages are attempted one by one; a
    package that cannot be installed is reported in the warnings and the
    settings that need it report their own failure, while the remaining
    settings still apply.
    """

    cfg = ctx.config.kde_settings
    engine = ctx.config.engine
    timeout = engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    changed = False
    warnings: list[str] = []
    packages_failed = False

    for package in cfg.packages:
        if package_is_installed(engine, package, timeout):
            continue
        _log(f"installing {package}")
        ok, error = install_package_once(engine, package, timeout)
        if not ok:
            packages_failed = True
            warning = f"cannot install {package}: {error}"
            _log(warning)
            warnings.append(warning)
        else:
            changed = True

    if packages_failed:
        _log(
            "a package is missing, the settings that need it are reported as"
            " warnings and the remaining settings still apply"
        )

    def step(description: str, call: Callable[[], bool]) -> bool:
        return _run_settings_step(warnings, description, call)

    try:
        run_command(
            _as_user_command(
                cfg,
                substituted_command(
                    cfg.mkdir_command,
                    {"path": str(Path(cfg.home_dir) / cfg.user_config_dir)},
                ),
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
        "set the touchpad click method",
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
    # The combinations are applied after the scripts are enabled: enabling
    # applies live and makes kwin register the combinations of the scripts at
    # once, and the shared client then takes each claimed combination from
    # whatever action holds it and gives it to the action of its script, so a
    # script is never left with a refused registration.
    kwin_scripts_changed = step(
        "install and enable the kwin scripts",
        lambda: _apply_kwin_scripts(
            cfg,
            task_data_dir(ctx.repo_root, ctx.task_name)
            / cfg.kwin_scripts_dir_name,
            timeout=timeout,
            force=force,
            env=apply_env,
            warnings=warnings,
        ),
    )
    settings_changed |= kwin_scripts_changed
    settings_changed |= step(
        "apply the configured shortcuts",
        lambda: _apply_shortcuts_live(
            cfg,
            client_path=(
                task_data_dir(
                    ctx.repo_root, cfg.kglobalaccel_client_section_name
                )
                / cfg.kglobalaccel_client_file_name
            ),
            timeout=timeout,
            env=apply_env,
            system_python=ctx.config.engine.system_python,
            kglobalaccel_names=kglobalaccel_names(ctx.config.engine),
            warnings=warnings,
        ),
    )
    settings_changed |= step(
        "write the kwin script hotkey records",
        lambda: _write_script_hotkey_records(
            cfg, timeout=timeout, force=force, warnings=warnings
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
            task_data_dir(ctx.repo_root, ctx.task_name)
            / cfg.konsole_profile_file_name,
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
        script_path=(
            task_data_dir(ctx.repo_root, ctx.task_name)
            / cfg.desktop_ids_script_file_name
        ),
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
