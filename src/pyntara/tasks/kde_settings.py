"""Task kde_settings: apply the KDE appearance and input settings.

The task applies the configured dark appearance as the target user: the
color scheme that turns every Qt and KDE window dark and the global theme
that covers the whole desktop (panel, widgets, window decorations, icons).
Both values are applied with the plasma-apply tools through runuser, so
the config files stay owned by that user. The task also applies the input
and keyboard settings as KConfig values with kwriteconfig6: the NumLock
state on startup, the touchpad preferences (to every touchpad found) and
the Wayland virtual keyboard. When a desktop session is running the
changes apply immediately; without a session the tools still write the
config and the settings apply after the next login. The task is
idempotent: it reads the current values with kreadconfig6 and applies only
what differs. Missing packages (the provider of the plasma-apply tools and
the KConfig tools) are installed first.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

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

# The kdeglobals groups and keys that carry the applied theme values.
GENERAL_GROUP: tuple[str, ...] = ("General",)
KDE_GROUP: tuple[str, ...] = ("KDE",)
# The KConfig files the input and keyboard settings live in and their
# groups.
KCINPUTRC_FILE = "kcminputrc"
KWINRC_FILE = "kwinrc"
PLASMA_KEYBOARD_RC = "plasmakeyboardrc"
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


def task(ctx: Context) -> TaskResult:
    """Apply the dark appearance and the input and keyboard settings.

    The goal is reached when every configured value already matches and the
    packages are installed; the task then returns changed=False. Otherwise
    it installs missing packages and applies the differing values as the
    target user: the global theme first, then the color scheme so the
    configured scheme wins, then the NumLock state, the touchpad
    preferences and the Wayland virtual keyboard. Any failure is returned
    as an error TaskResult.
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
    try:
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
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(success=False, error=f"cannot apply KDE settings: {exc}")
    changed |= settings_changed

    if virtual_keyboard_changed:
        reload_error = _reload_kwin(cfg, timeout=timeout, env=apply_env)
        if reload_error is not None:
            return TaskResult(success=False, error=reload_error)

    if not changed:
        return TaskResult(success=True, changed=False, message="already configured")
    return TaskResult(
        success=True,
        changed=True,
        message="KDE appearance and input settings configured",
    )
