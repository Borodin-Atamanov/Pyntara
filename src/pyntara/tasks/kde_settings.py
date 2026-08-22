"""Task kde_settings: apply the dark color scheme and the dark global theme.

The task applies the configured dark appearance as the target user: the
color scheme that turns every Qt and KDE window dark and the global theme
that covers the whole desktop (panel, widgets, window decorations, icons).
Both values are applied with the plasma-apply tools through runuser, so
the config files stay owned by that user. When a desktop session is
running the changes apply immediately; without a session the tools still
write the config and the theme applies after the next login. The task is
idempotent: it reads the current values with kreadconfig6 and applies only
what differs. Missing packages (the provider of the plasma-apply tools and
the KConfig reader) are installed first.
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


def _as_user_command(cfg: KdeSettingsConfig, command: list[str]) -> list[str]:
    """Prefix a command with runuser so it runs as the target user."""

    return ["runuser", "-u", cfg.username, "--", *command]


def _home_env(cfg: KdeSettingsConfig) -> dict[str, str]:
    """Environment that points the KDE tools at the target user home."""

    return {"HOME": cfg.home_dir}


def _kreadconfig(
    cfg: KdeSettingsConfig,
    group_segments: tuple[str, ...],
    key: str,
    timeout: float,
) -> str:
    """Current value of one kdeglobals key, or an empty string when unset."""

    command = ["kreadconfig6", "--file", "kdeglobals"]
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

    current = _kreadconfig(cfg, KDE_GROUP, "LookAndFeelPackage", timeout)
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

    current = _kreadconfig(cfg, GENERAL_GROUP, "ColorScheme", timeout)
    if not force and current == cfg.color_scheme:
        return False
    run_command(
        _as_user_command(cfg, ["plasma-apply-colorscheme", cfg.color_scheme]),
        extra_env=env,
        timeout=timeout,
    )
    _log(f"applied color scheme: {cfg.color_scheme}")
    return True


def task(ctx: Context) -> TaskResult:
    """Apply the dark color scheme and the dark global theme.

    The goal is reached when both kdeglobals values already match the
    configuration and the packages are installed; the task then returns
    changed=False. Otherwise it installs missing packages and applies the
    differing values as the target user: the global theme first, then the
    color scheme so the configured scheme wins. Any failure is returned
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
        _log("no desktop session found, theme applies after login")

    theme_changed = False
    try:
        theme_changed |= _apply_look_and_feel(
            cfg, env=apply_env, timeout=timeout, force=force
        )
        theme_changed |= _apply_color_scheme(
            cfg, env=apply_env, timeout=timeout, force=force
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(success=False, error=f"cannot apply KDE theme: {exc}")
    changed |= theme_changed

    if not changed:
        return TaskResult(success=True, changed=False, message="already configured")
    return TaskResult(
        success=True,
        changed=True,
        message="KDE color scheme and global theme configured",
    )
