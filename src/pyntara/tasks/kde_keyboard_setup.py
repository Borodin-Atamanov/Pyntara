"""Task kde_keyboard_setup: configure KDE keyboard layouts and indicator.

The task writes the desktop keyboard layout settings with kwriteconfig6 as
the target user: the layout list, the XKB switch option (Caps Lock to the
first layout, Shift+Caps Lock to the second) and the enabled switch into
kxkbrc, and the indicator display style (the country flag) into the
keyboard layout applet of the Plasma panel. kwriteconfig6 runs as the
configured user through runuser, so the config files stay owned by that
user. When a value changed, the task reloads the kwin configuration and
restarts the Plasma panel, so the settings apply immediately. The task is
idempotent: it compares every value with kreadconfig6 and writes only what
differs. Missing packages (the kwriteconfig6 provider and the DBus client)
are installed first. A desktop session that cannot be found disables the
reload: the settings then apply after the next login.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pyntara.config import KdeKeyboardSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_package_once,
    package_is_installed,
    run_command,
    trim_whitespace,
)

# The kxkbrc group that carries the layout settings.
KXKBRC_GROUP: tuple[str, ...] = ("Layout",)


def _as_user_command(cfg: KdeKeyboardSetupConfig, command: list[str]) -> list[str]:
    """Prefix a command with runuser so it runs as the target user."""

    return ["runuser", "-u", cfg.username, "--", *command]


def _home_env(cfg: KdeKeyboardSetupConfig) -> dict[str, str]:
    """Environment that points the KDE tools at the target user home."""

    return {"HOME": cfg.home_dir}


def _kreadconfig(
    cfg: KdeKeyboardSetupConfig,
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
    cfg: KdeKeyboardSetupConfig,
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


def _keyboard_layout_config_group(text: str, plugin: str) -> tuple[str, ...] | None:
    """The Configuration/General group of the applet that declares plugin.

    Plasma appletsrc nests groups as [Containments][X][Applets][Y]; the
    applet whose section declares plugin=<plugin> holds its configuration
    in [Configuration][General] below that section. Returns the group
    segments or None when no applet declares the plugin.
    """

    current: tuple[str, ...] = ()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = tuple(part for part in line[1:-1].split("][") if part)
        elif line == f"plugin={plugin}":
            return current + ("Configuration", "General")
    return None


def _read_process_environment(pid: str) -> str | None:
    """DBUS_SESSION_BUS_ADDRESS of a process, or None when unreadable."""

    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    for entry in data.split(b"\0"):
        if entry.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
            return entry.split(b"=", 1)[1].decode("utf-8")
    return None


def _session_bus_address(cfg: KdeKeyboardSetupConfig, timeout: float) -> str | None:
    """The DBus session address of the target user, or None.

    The address is read from the environment of the user's kwin_wayland
    process, which owns the session keyboard layout service. A missing
    session (no kwin_wayland process) returns None.
    """

    result = run_command(
        ["pgrep", "-u", cfg.username, "-x", "kwin_wayland"],
        check=False,
        capture=True,
        timeout=timeout,
    )
    lines = trim_whitespace(result.stdout).splitlines()
    if not lines:
        return None
    return _read_process_environment(lines[0].strip())


def _reload_kwin(
    cfg: KdeKeyboardSetupConfig,
    *,
    timeout: float,
    home_env: dict[str, str],
) -> str | None:
    """Reload the kwin keyboard layout config; error text or None.

    The reload runs through the target user's session bus so kwin re-reads
    kxkbrc immediately. A missing session is not an error: the settings
    apply after the next login. A failing reload command is an error.
    """

    bus = _session_bus_address(cfg, timeout)
    if bus is None:
        _log("no desktop session found, layouts apply after login")
        return None
    try:
        run_command(
            _as_user_command(cfg, list(cfg.kwin_reload_command)),
            extra_env={**home_env, "DBUS_SESSION_BUS_ADDRESS": bus},
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot reload kwin layouts: {exc}"
    _log("reloaded kwin keyboard layouts")
    return None


def task(ctx: Context) -> TaskResult:
    """Write the KDE keyboard layout settings; skip when already reached.

    The goal is reached when every kxkbrc value and the indicator display
    style already match the configuration and the packages are installed;
    the task then returns changed=False. Otherwise it installs missing
    packages, writes the differing values as the target user and reloads
    kwin and the Plasma panel so the settings apply immediately. Any
    failure is returned as an error TaskResult.
    """

    cfg = ctx.config.kde_keyboard_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = "kde_keyboard_setup" in ctx.force_tasks
    home_env = _home_env(cfg)
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
        _as_user_command(cfg, ["mkdir", "-p", cfg.config_dir]),
        extra_env=home_env,
        timeout=timeout,
    )

    layout_changed = False
    try:
        layout_changed |= _sync_key(
            cfg,
            KXKBRC_GROUP,
            "LayoutList",
            ",".join(cfg.layouts),
            timeout=timeout,
            force=force,
            bool_value=False,
        )
        layout_changed |= _sync_key(
            cfg,
            KXKBRC_GROUP,
            "Options",
            cfg.switch_option,
            timeout=timeout,
            force=force,
            bool_value=False,
        )
        layout_changed |= _sync_key(
            cfg,
            KXKBRC_GROUP,
            "Use",
            "true" if cfg.use_layout_switching else "false",
            timeout=timeout,
            force=force,
            bool_value=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(success=False, error=f"cannot write kxkbrc: {exc}")
    changed |= layout_changed

    applet_changed = False
    appletsrc_path = Path(cfg.config_dir) / cfg.appletsrc_file_name
    try:
        group = _keyboard_layout_config_group(
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
                cfg, cfg.appletsrc_file_name, group, "displayStyle", timeout
            )
            if force or current != cfg.indicator_display_style:
                _kwriteconfig(
                    cfg,
                    cfg.appletsrc_file_name,
                    group,
                    "displayStyle",
                    cfg.indicator_display_style,
                    timeout=timeout,
                    bool_value=False,
                )
                _log(f"set indicator display style: {cfg.indicator_display_style}")
                applet_changed = True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                error=f"cannot write {cfg.appletsrc_file_name}: {exc}",
            )
    changed |= applet_changed

    if layout_changed:
        reload_error = _reload_kwin(cfg, timeout=timeout, home_env=home_env)
        if reload_error is not None:
            return TaskResult(success=False, error=reload_error)

    if applet_changed:
        try:
            run_command(list(cfg.panel_restart_command), timeout=timeout)
            _log("restarted Plasma panel")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(success=False, error=f"cannot restart panel: {exc}")

    if not changed:
        return TaskResult(success=True, changed=False, message="already configured")
    return TaskResult(
        success=True,
        changed=True,
        message="KDE keyboard layouts and layout indicator configured",
    )
