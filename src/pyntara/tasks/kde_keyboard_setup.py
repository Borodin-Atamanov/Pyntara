"""Task kde_keyboard_setup: configure KDE keyboard layouts and indicator.

The task writes the desktop keyboard layout settings with kwriteconfig6 as
the target user: the complete kxkbrc [Layout] group as KDE produces it
(the layout list, the XKB switch option, the reset flag, the switch mode
and the per-layout empty display names and variants), so kwin applies the
switch option at the next session start, and the indicator display style
(the country flag) into the keyboard layout applet of the Plasma panel.
kwriteconfig6 runs as the configured user through runuser, so the config
files stay owned by that user. When a value changed, the task reloads the
kwin configuration and restarts the Plasma panel; the layout list applies
at once, the switch option at the next login. The task is idempotent: it
compares every value with kreadconfig6 and writes only what differs.
Missing packages (the kwriteconfig6 provider and the DBus client) are
installed first. A desktop session that cannot be found disables the
reload: the settings then apply after the next login.

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

from pyntara.config import EngineConfig, KdeKeyboardSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_package_once,
    package_is_installed,
    run_command,
    session_bus_address,
    substituted_command,
    task_data_dir,
    trim_whitespace,
)

# The kxkbrc group that carries the layout settings, the KConfig file of
# the global shortcuts, the keys the task writes and the Qt modifier
# flags all come from the config: they are the vocabulary of foreign
# files, so they are values like any other.


def _as_user_command(cfg: KdeKeyboardSetupConfig, command: list[str]) -> list[str]:
    """Prefix a command with the configured wrapper of the target user.

    The wrapper is a config value of the section, so a machine whose
    desktop user is reached another way is a config change.
    """

    return [
        *substituted_command(cfg.runuser_command, {"username": cfg.username}),
        *command,
    ]


def _home_env(cfg: KdeKeyboardSetupConfig) -> dict[str, str]:
    """Environment that points the KDE tools at the target user home."""

    return {"HOME": cfg.home_dir}


def _session_bus_env(
    cfg: KdeKeyboardSetupConfig, engine: EngineConfig
) -> dict[str, str]:
    """The one-entry environment that reaches the live session bus.

    The bus address comes from the session manager of the desktop user, so the
    DBus clients of the task work the same whether the run started inside the
    session or over a remote console. An empty dict means no live session was
    found: the persistent values are still written and apply at the next login.
    """

    bus = session_bus_address(
        cfg.username,
        command_template=engine.session_environment_command,
        keys=engine.session_environment_keys,
        bus_key=engine.session_bus_key,
        timeout=engine.process_check_timeout_seconds,
    )
    if bus is None:
        return {}
    return {engine.session_bus_key: bus}


def _per_layout_empty_list(layouts: tuple[str, ...]) -> str:
    """The kxkbrc comma list of empty display names or variants, one per layout."""

    return ",".join([""] * len(layouts))


def _kconfig_command(
    cfg: KdeKeyboardSetupConfig,
    base_command: tuple[str, ...],
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
) -> list[str]:
    """One KConfig call: the configured base, the groups and the key.

    The base call carries the file name and every selector is a config
    value, so another KConfig version or another tool is a config change.
    The reader and the writer share this builder, so the two calls can
    never drift apart.
    """

    command = substituted_command(base_command, {"file_name": file_name})
    for segment in group_segments:
        command.extend(
            substituted_command(cfg.config_group_flag, {"group": segment})
        )
    command.extend(substituted_command(cfg.config_key_flag, {"key": key}))
    return command


def _kreadconfig(
    cfg: KdeKeyboardSetupConfig,
    file_name: str,
    group_segments: tuple[str, ...],
    key: str,
    timeout: float,
) -> str:
    """Current value of one KConfig key, or an empty string when unset."""

    command = _kconfig_command(
        cfg, cfg.kreadconfig_command, file_name, group_segments, key
    )
    result = run_command(
        _as_user_command(cfg, command),
        extra_env=_home_env(cfg),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return trim_whitespace(result.stdout)


def _kwriteconfig(
    cfg: KdeKeyboardSetupConfig,
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
        cfg, cfg.kwriteconfig_command, file_name, group_segments, key
    )
    if bool_value:
        command.extend(cfg.config_bool_type_flag)
    command.append(value)
    run_command(
        _as_user_command(cfg, command),
        extra_env=_home_env(cfg),
        timeout=timeout,
    )


def _sync_key(
    cfg: KdeKeyboardSetupConfig,
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

    current = _kreadconfig(cfg, cfg.kxkbrc_file_name, group_segments, key, timeout)
    if not force and current == target:
        return False
    _kwriteconfig(
        cfg,
        cfg.kxkbrc_file_name,
        group_segments,
        key,
        target,
        timeout=timeout,
        bool_value=bool_value,
    )
    _log(f"set {key}: {target}")
    return True


def _keyboard_layout_config_group(
    cfg: KdeKeyboardSetupConfig, text: str, plugin: str
) -> tuple[str, ...] | None:
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
            return current + cfg.applet_configuration_group
    return None


def _reload_kwin(
    cfg: KdeKeyboardSetupConfig,
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
            _as_user_command(cfg, list(cfg.kwin_reload_command)),
            extra_env={**home_env, **bus_env},
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot reload kwin layouts: {exc}"
    _log("reloaded kwin keyboard layouts")
    return None


def _shortcut_to_combined(cfg: KdeKeyboardSetupConfig, shortcut: str) -> int | None:
    """The combined Qt key code of a portable shortcut, or None.

    Only the shortcuts the daemon accepts are supported: any modifiers
    from Ctrl, Alt, Shift and Meta plus one alphanumeric key. Other
    portable forms (function keys, named keys) return None; the caller
    then still writes the shortcut to the config file, it just cannot be
    applied live. The modifier flags come from the config.
    """

    parts = [part for part in shortcut.split("+") if part]
    modifiers = 0
    key: int | None = None
    for part in parts:
        if part in cfg.shortcut_modifier_bits:
            modifiers |= cfg.shortcut_modifier_bits[part]
        elif key is None and len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        else:
            return None
    if key is None:
        return None
    return modifiers | key


def _sync_hotkey_file(
    cfg: KdeKeyboardSetupConfig,
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
    group = (cfg.layout_switcher_component_unique,)
    for action, shortcut in shortcuts.items():
        value = f"{shortcut},none,{action}"
        current = _kreadconfig(
            cfg, cfg.shortcuts_file_name, group, action, timeout
        )
        if not force and current == value:
            continue
        _kwriteconfig(
            cfg,
            cfg.shortcuts_file_name,
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
    cfg: KdeKeyboardSetupConfig,
    shortcuts: dict[str, str],
    *,
    script_path: Path,
    timeout: float,
    home_env: dict[str, str],
    bus_env: dict[str, str],
    system_python: str,
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

    assign: list[tuple[str, int]] = []
    for action, shortcut in shortcuts.items():
        combined = _shortcut_to_combined(cfg, shortcut)
        if combined is None:
            _log(f"hotkey {action} is not applicable live, applies at login")
            continue
        assign.append((action, combined))
    if not assign:
        return None, False
    payload = json.dumps(
        {
            "component_unique": cfg.layout_switcher_component_unique,
            "component_friendly": cfg.layout_switcher_component_friendly,
            "assign": assign,
        }
    )
    try:
        result = run_command(
            _as_user_command(
                cfg,
                [
                    *substituted_command(
                        cfg.python_script_command, {"python": system_python}
                    ),
                    script_path.read_text(encoding="utf-8"),
                    payload,
                ],
            ),
            extra_env={**home_env, **bus_env},
            timeout=timeout,
            capture=True,
        )
    except OSError as exc:
        return f"cannot read the hotkey script {script_path}: {exc}", False
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
    before = report.get("before", {})
    after = report.get("after", {})
    changed = False
    for action, combined in assign:
        if after.get(action) != [combined]:
            return (
                f"cannot apply hotkey {action}: daemon reports {after.get(action)}",
                False,
            )
        if before.get(action) != after.get(action):
            changed = True
    return None, changed


def task(ctx: Context) -> TaskResult:
    """Write the KDE keyboard layout settings; warn instead of failing.

    The goal is reached when every kxkbrc value and the indicator display
    style already match the configuration and the packages are installed;
    the task then returns changed=False. Otherwise it installs missing
    packages, writes the differing values as the target user and reloads
    kwin and the Plasma panel so the settings apply immediately. A step
    that cannot be performed is reported as a warning and the remaining
    independent steps still run, because a recoverable failure must never
    stop the provisioning.
    """

    cfg = ctx.config.kde_keyboard_setup
    engine = ctx.config.engine
    timeout = engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    home_env = _home_env(cfg)
    bus_env = _session_bus_env(cfg, engine)
    changed = False
    warnings: list[str] = []

    for package in cfg.packages:
        if package_is_installed(engine, package, timeout):
            continue
        _log(f"installing {package}")
        ok, error = install_package_once(engine, package, timeout)
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
                cfg,
                substituted_command(
                    cfg.mkdir_command, {"path": cfg.config_dir}
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
            warnings=(f"cannot create {cfg.config_dir}: {exc}",),
        )

    layout_changed = False
    for key, target, bool_value in (
        (cfg.kxkbrc_key_layout_list, ",".join(cfg.layouts), False),
        (cfg.kxkbrc_key_display_names, _per_layout_empty_list(cfg.layouts), False),
        (cfg.kxkbrc_key_variant_list, _per_layout_empty_list(cfg.layouts), False),
        (cfg.kxkbrc_key_options, cfg.switch_option, False),
        (
            cfg.kxkbrc_key_reset_old_options,
            cfg.kconfig_true_value if cfg.reset_old_options else cfg.kconfig_false_value,
            True,
        ),
        (cfg.kxkbrc_key_switch_mode, cfg.switch_mode, False),
        (
            cfg.kxkbrc_key_use,
            (
                cfg.kconfig_true_value
                if cfg.use_layout_switching
                else cfg.kconfig_false_value
            ),
            True,
        ),
    ):
        try:
            layout_changed |= _sync_key(
                cfg,
                cfg.kxkbrc_group,
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
    appletsrc_path = Path(cfg.config_dir) / cfg.appletsrc_file_name
    try:
        group = _keyboard_layout_config_group(
            cfg,
            appletsrc_path.read_text(encoding="utf-8"), cfg.applet_plugin
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
                cfg, cfg.appletsrc_file_name, group, cfg.display_style_key, timeout
            )
            if force or current != cfg.indicator_display_style:
                _kwriteconfig(
                    cfg,
                    cfg.appletsrc_file_name,
                    group,
                    cfg.display_style_key,
                    cfg.indicator_display_style,
                    timeout=timeout,
                    bool_value=False,
                )
                _log(f"set indicator display style: {cfg.indicator_display_style}")
                applet_changed = True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot write {cfg.appletsrc_file_name}: {exc}")
    changed |= applet_changed

    hotkeys_changed = False
    if cfg.layout_switch_shortcuts:
        try:
            hotkeys_changed |= _sync_hotkey_file(
                cfg,
                cfg.layout_switch_shortcuts,
                timeout=timeout,
                force=force,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot write {cfg.shortcuts_file_name}: {exc}")
        if not bus_env:
            _log("no desktop session found, layout hotkeys apply at login")
        else:
            hotkey_error, applied = _apply_hotkeys_live(
                cfg,
                cfg.layout_switch_shortcuts,
                script_path=(
                    task_data_dir(ctx.repo_root, ctx.task_name)
                    / cfg.apply_hotkeys_script_file_name
                ),
                timeout=timeout,
                home_env=home_env,
                bus_env=bus_env,
                system_python=engine.system_python,
            )
            if hotkey_error is not None:
                warnings.append(hotkey_error)
            else:
                hotkeys_changed |= applied
    changed |= hotkeys_changed

    if layout_changed:
        reload_error = _reload_kwin(
            cfg, timeout=timeout, home_env=home_env, bus_env=bus_env
        )
        if reload_error is not None:
            warnings.append(reload_error)
        _log("the layout switch option takes effect at the next login")

    if applet_changed:
        try:
            run_command(list(cfg.panel_restart_command), timeout=timeout)
            _log("restarted Plasma panel")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot restart panel: {exc}")

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
