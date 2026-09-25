"""Task kde_keyboard_setup: configure KDE keyboard layouts and indicator.

The task writes the desktop keyboard layout settings with kwriteconfig6 as
the target user: the complete kxkbrc [Layout] group as KDE produces it
(the layout list, the XKB switch option, the reset flag, the switch mode
and the per-layout empty display names and variants), so kwin applies the
switch option at the next session start, and the indicator display style
(the country flag) into the keyboard layout applet of the Plasma panel.
kwriteconfig6 runs as the configured user through runuser, so the config
files stay owned by that user. When a value changed, the task reloads the
kwin configuration, which applies the kwinrc values of a running session;
the layout values behave differently: kwin builds its keymap from kxkbrc
when it starts and offers no live reload of the layout list or of the
switch option, so both take effect at the next start of the session. The
task is idempotent: it compares every value with kreadconfig6 and writes
only what differs. Missing packages (the kwriteconfig6 provider and the
DBus client) are installed first. A desktop session that cannot be found
disables the reload: the settings then apply after the next login.

Optional per-layout hotkeys (layout_switch_shortcuts) are written to
kglobalshortcutsrc the same way; when a desktop session is running, the
supported shortcuts are also applied through the kglobalaccel daemon with
python3-dbus, which frees the key from its current owner and makes the
shortcut work immediately without a session restart.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_package_once,
    kglobalaccel_names,
    package_is_installed,
    render_client_file,
    run_command,
    session_bus_address,
    substituted_command,
    task_data_dir,
    trim_whitespace,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import kde_keyboard_setup as values
from pyntara.values import missing_value_names

# The kxkbrc group that carries the layout settings, the KConfig file of the
# global shortcuts, the keys the task writes and the Qt modifier flags all come
# from the values module: they are the vocabulary of foreign files, so they are
# values like any other.


def _as_user_command(command: list[str]) -> list[str]:
    """Prefix a command with the configured wrapper of the target user.

    The wrapper is a value of the section, so a machine whose desktop user is
    reached another way is a value change.
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


def _session_bus_env() -> dict[str, str]:
    """The one-entry environment that reaches the live session bus.

    The bus address comes from the session manager of the desktop user, so the
    DBus clients of the task work the same whether the run started inside the
    session or over a remote console. An empty dict means no live session was
    found: the persistent values are still written and apply at the next login.
    """

    bus = session_bus_address(
        common_values.DESKTOP_USERNAME,
        command_template=engine_values.SESSION_ENVIRONMENT_COMMAND,
        keys=engine_values.SESSION_ENVIRONMENT_KEYS,
        bus_key=engine_values.SESSION_BUS_KEY,
        timeout=engine_values.PROCESS_CHECK_TIMEOUT_SECONDS,
    )
    if bus is None:
        return {}
    return {engine_values.SESSION_BUS_KEY: bus}


def _per_layout_empty_list(layouts: tuple[str, ...]) -> str:
    """The kxkbrc comma list of empty display names or variants, one per layout."""

    return ",".join([""] * len(layouts))


def _kconfig_command(
    base_command: tuple[str, ...],
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
) -> list[str]:
    """One KConfig call: the configured base, the groups and the key.

    The base call carries the file name and every selector is a value, so
    another KConfig version or another tool is a value change. The reader and
    the writer share this builder, so the two calls can never drift apart.
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


def _kwriteconfig(
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    value: str,
    *,
    timeout: float,
    bool_value: bool,
) -> None:
    """Write one KConfig key with the configured writer as the target user."""

    command = _kconfig_command(
        values.KWRITECONFIG_COMMAND, file_name, group_segments, key
    )
    if bool_value:
        command.extend(values.CONFIG_BOOL_TYPE_FLAG)
    command.append(value)
    run_command(
        _as_user_command(command),
        extra_env=_home_env(),
        timeout=timeout,
    )


def _sync_key(
    group_segments: tuple[str, ...],
    key: str,
    target: str,
    *,
    timeout: float,
    force: bool,
    bool_value: bool,
) -> bool:
    """Write the kxkbrc key when it differs; True when a write happened.

    kreadconfig6 returns the current value; the write is skipped when the
    value already matches, so repeated runs change nothing. Force mode
    always writes.
    """

    current = _kreadconfig(values.KXKBRC_FILE_NAME, group_segments, key, timeout)
    if not force and current == target:
        return False
    _kwriteconfig(
        values.KXKBRC_FILE_NAME,
        group_segments,
        key,
        target,
        timeout=timeout,
        bool_value=bool_value,
    )
    _log(f"set {key}: {target}")
    return True


def _keyboard_layout_config_group(text: str, plugin: str) -> tuple[str, ...] | None:
    """The Configuration/General group of the applet that declares plugin.

    Plasma appletsrc nests groups as [Containments][X][Applets][Y]; the
    applet whose section declares plugin=<plugin> holds its configuration
    in the configured group below that section. Returns the group
    segments or None when no applet declares the plugin.
    """

    current: tuple[str, ...] = ()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = tuple(part for part in line[1:-1].split("][") if part)
        elif line == f"plugin={plugin}":
            return current + values.APPLET_CONFIGURATION_GROUP
    return None


def _reload_kwin(
    *,
    timeout: float,
    home_env: dict[str, str],
    bus_env: dict[str, str],
) -> str | None:
    """Reload the kwin keyboard layout config; error text or None.

    The reload runs through the target user's session bus so kwin re-reads
    kxkbrc immediately; the layout list applies at once, the switch option
    takes effect at the next session start. A missing session bus is not an
    error: the settings apply after the next login. A failing reload
    command is an error.
    """

    if not bus_env:
        _log("no desktop session found, layouts apply after login")
        return None
    try:
        run_command(
            _as_user_command(list(values.KWIN_RELOAD_COMMAND)),
            extra_env={**home_env, **bus_env},
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot reload kwin layouts: {exc}"
    _log("reloaded kwin keyboard layouts")
    return None


def _shortcut_to_combined(shortcut: str) -> int | None:
    """The combined Qt key code of a portable shortcut, or None.

    Only the shortcuts the daemon accepts are supported: any modifiers
    from Ctrl, Alt, Shift and Meta plus one alphanumeric key. Other
    portable forms (function keys, named keys) return None; the caller
    then still writes the shortcut to the config file, it just cannot be
    applied live. The modifier flags come from the values module.
    """

    parts = [part for part in shortcut.split("+") if part]
    modifiers = 0
    key: int | None = None
    for part in parts:
        if part in values.SHORTCUT_MODIFIER_BITS:
            modifiers |= values.SHORTCUT_MODIFIER_BITS[part]
        elif key is None and len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        else:
            return None
    if key is None:
        return None
    return modifiers | key


def _sync_hotkey_file(
    shortcuts: dict[str, str],
    *,
    timeout: float,
    force: bool,
) -> bool:
    """Write the kglobalshortcutsrc hotkey entries; True on any write.

    Each configured action gets its entry (active key, default key none,
    friendly name) so the shortcut survives a login without a session.
    The write is skipped when the entry already matches.
    """

    changed = False
    group = (values.LAYOUT_SWITCHER_COMPONENT_UNIQUE,)
    for action, shortcut in shortcuts.items():
        value = f"{shortcut},none,{action}"
        current = _kreadconfig(
            common_values.SHORTCUTS_FILE_NAME, group, action, timeout
        )
        if not force and current == value:
            continue
        _kwriteconfig(
            common_values.SHORTCUTS_FILE_NAME,
            group,
            action,
            value,
            timeout=timeout,
            bool_value=False,
        )
        _log(f"set hotkey {action}: {shortcut}")
        changed = True
    return changed


# The python3-dbus client that applies hotkeys through the running
# kglobalaccel daemon. It runs as the target user on the desktop session
# bus. The payload is one JSON argument: the component names and a list
# of [action unique name, combined key code] pairs. The script frees each
# key from its current owner, assigns it to the configured action and
# prints the before and after state as JSON. The actionId field order is
# [component unique, action unique, component friendly, action friendly];
# the daemon silently ignores a wrong order, so it must not change.


def _apply_hotkeys_live(
    shortcuts: dict[str, str],
    *,
    script_path: Path,
    timeout: float,
    home_env: dict[str, str],
    bus_env: dict[str, str],
    system_python: str,
    kglobalaccel_names: dict[str, str],
) -> tuple[str | None, bool]:
    """Apply the supported hotkeys through the running daemon.

    Runs the python3-dbus script named by script_path, which ships under
    task_data/ of the clone, as the target user on the desktop session bus;
    the script frees each key from its current owner and assigns it to the
    configured action, so the shortcut works without a session restart.
    Shortcuts the parser does not support are skipped here (they were
    already written to kglobalaccutsrc). Returns error text or None and
    whether a shortcut actually changed.
    """

    changes: list[tuple[str, str]] = []
    for action, shortcut in shortcuts.items():
        if _shortcut_to_combined(shortcut) is None:
            _log(f"hotkey {action} is not applicable live, applies at login")
            continue
        changes.append((action, shortcut))
    if not changes:
        return None, False
    payload = json.dumps(
        {
            "changes": [
                {
                    "component_unique": values.LAYOUT_SWITCHER_COMPONENT_UNIQUE,
                    "component_friendly": (values.LAYOUT_SWITCHER_COMPONENT_FRIENDLY),
                    "action": action,
                    "keys": [shortcut],
                }
                for action, shortcut in changes
            ]
        }
    )
    rendered_client = render_client_file(script_path, kglobalaccel_names)
    if isinstance(rendered_client, str):
        return rendered_client, False
    try:
        result = run_command(
            _as_user_command(
                [
                    *substituted_command(
                        values.PYTHON_SCRIPT_COMMAND,
                        {"python": system_python, "client_file": str(rendered_client)},
                    ),
                    payload,
                ],
            ),
            extra_env={**home_env, **bus_env},
            timeout=timeout,
            capture=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = trim_whitespace(exc.stderr or "")
        suffix = f": {detail}" if detail else ""
        return f"cannot apply layout hotkeys: {exc}{suffix}", False
    except subprocess.TimeoutExpired as exc:
        return f"cannot apply layout hotkeys: {exc}", False
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return f"cannot parse kglobalaccel reply: {result.stdout}", False
    results = list(report.get("results") or [])
    if len(results) != len(changes):
        counted = f"{len(results)} of {len(changes)}"
        return (
            f"cannot apply layout hotkeys: the client reported {counted}",
            False,
        )
    changed = False
    for (action, shortcut), item in zip(changes, results):
        if item.get("missing"):
            return (
                f"cannot apply hotkey {action}: the daemon does not know it",
                False,
            )
        if item.get("unsupported"):
            return (
                f"cannot apply hotkey {action}: {shortcut} is unreadable",
                False,
            )
        if item.get("after") != item.get("requested"):
            return (
                f"cannot apply hotkey {action}: daemon reports {item.get('after')}",
                False,
            )
        if item.get("before") != item.get("after"):
            changed = True
    return None, changed


def task(ctx: Context) -> TaskResult:
    """Write the KDE keyboard layout settings; warn instead of failing.

    The goal is reached when every kxkbrc value and the indicator display
    style already match the configuration and the packages are installed;
    the task then returns changed=False. Otherwise it installs missing
    packages, writes the differing values as the target user and reloads
    kwin so a running session reads the kwinrc values. A step
    that cannot be performed is reported as a warning and the remaining
    independent steps still run, because a recoverable failure must never
    stop the provisioning.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES) + missing_value_names(
        common_values, common_values.READ_VALUE_NAMES
    )
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read.
        return TaskResult(
            success=True,
            message=(
                "the kde_keyboard_setup values are not declared, nothing was changed"
            ),
            warnings=(
                "the kde_keyboard_setup values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    home_env = _home_env()
    bus_env = _session_bus_env()
    changed = False
    warnings: list[str] = []

    for package in values.PACKAGES:
        if package_is_installed(package, timeout):
            continue
        _log(f"installing {package}")
        ok, error = install_package_once(package, timeout)
        if not ok:
            warnings.append(f"cannot install {package}: {error}")
        else:
            changed = True
    if warnings:
        # The provider of kwriteconfig6 and the DBus client is the
        # mechanism of the whole task; without it the writes and the
        # live apply cannot succeed, so the rest is skipped.
        return TaskResult(
            success=True,
            changed=changed,
            message="KDE keyboard layouts not configured",
            warnings=tuple(warnings),
        )

    try:
        run_command(
            _as_user_command(
                substituted_command(
                    values.MKDIR_COMMAND, {"path": str(values.CONFIG_DIR)}
                ),
            ),
            extra_env=home_env,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(
            success=True,
            changed=changed,
            message="KDE keyboard layouts not configured",
            warnings=(f"cannot create {values.CONFIG_DIR}: {exc}",),
        )

    layout_changed = False
    for key, target, bool_value in (
        (values.KXKBRC_KEY_LAYOUT_LIST, ",".join(values.LAYOUTS), False),
        (
            values.KXKBRC_KEY_DISPLAY_NAMES,
            _per_layout_empty_list(values.LAYOUTS),
            False,
        ),
        (
            values.KXKBRC_KEY_VARIANT_LIST,
            _per_layout_empty_list(values.LAYOUTS),
            False,
        ),
        (values.KXKBRC_KEY_OPTIONS, values.SWITCH_OPTION, False),
        (
            values.KXKBRC_KEY_RESET_OLD_OPTIONS,
            (
                common_values.KCONFIG_TRUE_VALUE
                if values.RESET_OLD_OPTIONS
                else common_values.KCONFIG_FALSE_VALUE
            ),
            True,
        ),
        (values.KXKBRC_KEY_SWITCH_MODE, values.SWITCH_MODE, False),
        (
            values.KXKBRC_KEY_USE,
            (
                common_values.KCONFIG_TRUE_VALUE
                if values.USE_LAYOUT_SWITCHING
                else common_values.KCONFIG_FALSE_VALUE
            ),
            True,
        ),
    ):
        try:
            layout_changed |= _sync_key(
                values.KXKBRC_GROUP,
                key,
                target,
                timeout=timeout,
                force=force,
                bool_value=bool_value,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot write kxkbrc {key}: {exc}")
    changed |= layout_changed

    applet_changed = False
    appletsrc_path = values.CONFIG_DIR / values.APPLETSRC_FILE_NAME
    try:
        group = _keyboard_layout_config_group(
            appletsrc_path.read_text(encoding="utf-8"), values.APPLET_PLUGIN
        )
    except OSError:
        group = None
    if group is None:
        _log(
            f"keyboard layout applet not found in {appletsrc_path}, "
            "indicator left as is"
        )
    else:
        try:
            current = _kreadconfig(
                values.APPLETSRC_FILE_NAME,
                group,
                values.DISPLAY_STYLE_KEY,
                timeout,
            )
            if force or current != values.INDICATOR_DISPLAY_STYLE:
                _kwriteconfig(
                    values.APPLETSRC_FILE_NAME,
                    group,
                    values.DISPLAY_STYLE_KEY,
                    values.INDICATOR_DISPLAY_STYLE,
                    timeout=timeout,
                    bool_value=False,
                )
                _log(f"set indicator display style: {values.INDICATOR_DISPLAY_STYLE}")
                applet_changed = True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot write {values.APPLETSRC_FILE_NAME}: {exc}")
    changed |= applet_changed

    hotkeys_changed = False
    if values.LAYOUT_SWITCH_SHORTCUTS:
        try:
            hotkeys_changed |= _sync_hotkey_file(
                values.LAYOUT_SWITCH_SHORTCUTS,
                timeout=timeout,
                force=force,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot write {common_values.SHORTCUTS_FILE_NAME}: {exc}")
        if not bus_env:
            _log("no desktop session found, layout hotkeys apply at login")
        else:
            hotkey_error, applied = _apply_hotkeys_live(
                values.LAYOUT_SWITCH_SHORTCUTS,
                script_path=(
                    task_data_dir(ctx.repo_root, ctx.task_name)
                    / values.APPLY_HOTKEYS_SCRIPT_FILE_NAME
                ),
                timeout=timeout,
                home_env=home_env,
                bus_env=bus_env,
                system_python=engine_values.SYSTEM_PYTHON,
                kglobalaccel_names=kglobalaccel_names(),
            )
            if hotkey_error is not None:
                warnings.append(hotkey_error)
            else:
                hotkeys_changed |= applied
    changed |= hotkeys_changed

    if layout_changed:
        reload_error = _reload_kwin(timeout=timeout, home_env=home_env, bus_env=bus_env)
        if reload_error is not None:
            warnings.append(reload_error)
        _log("the layout switch option takes effect at the next login")


    if warnings:
        message = (
            "KDE keyboard layouts configured with warnings"
            if changed
            else "KDE keyboard layouts not configured"
        )
        return TaskResult(
            success=True,
            changed=changed,
            message=message,
            warnings=tuple(warnings),
        )
    if not changed:
        return TaskResult(success=True, changed=False, message="already configured")
    return TaskResult(
        success=True,
        changed=True,
        message="KDE keyboard layouts and layout indicator configured",
    )
