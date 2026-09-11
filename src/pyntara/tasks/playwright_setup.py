"""Task playwright_setup: install the playwright-cli browser control tool.

The described goal is a playwright-cli available to the desktop user that
drives the Google Chrome installed by chrome_setup over its Chrome
DevTools Protocol listener on the loopback address. The task installs the
nodejs and npm packages from the Ubuntu archive (nodejs lives in the
universe component, enabled by add_extra_repos, a hard dependency of the
task) and then installs the npm package cli_package into the user prefix under
home_dir (user_prefix_relative_path) with the configured npm install
command, running it as the desktop user through the configured runuser
command. The binary lands at cli_bin_relative_path inside that prefix, so
no root-owned
npm prefix is used and the plain user can update it. The version is not
chased: npm installs the latest release, and a rerun whose configured apt
packages are installed and whose playwright-cli binary answers the
version command changes nothing (docs/spec/playwright-setup.md). Force
mode re-runs the npm install regardless of the current binary.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pyntara.config import PlaywrightSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_packages,
    package_is_installed,
    run_command,
    substituted_command,
)


def _user_prefix(cfg: PlaywrightSetupConfig) -> Path:
    """The npm prefix under the desktop user home."""

    return Path(cfg.home_dir) / cfg.user_prefix_relative_path


def _cli_bin_path(cfg: PlaywrightSetupConfig) -> Path:
    """The playwright-cli binary path inside the user prefix."""

    return _user_prefix(cfg) / cfg.cli_bin_relative_path


def _runuser_command(cfg: PlaywrightSetupConfig) -> list[str]:
    """The runuser prefix that runs a command as the desktop user."""

    return substituted_command(
        cfg.runuser_command,
        {"username": cfg.username, "home_dir": cfg.home_dir},
    )


def _cli_version(cfg: PlaywrightSetupConfig, *, timeout: float) -> str:
    """The playwright-cli version as the desktop user, or an empty string.

    The probe runs through runuser because the binary lives in the user
    prefix and node reads the user npm cache under HOME. A binary that is
    missing, not executable, or fails to report a version yields an empty
    string, which stands for not installed.
    """

    binary = _cli_bin_path(cfg)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return ""
    try:
        result = run_command(
            _runuser_command(cfg)
            + substituted_command(cfg.cli_version_command, {"cli_bin": str(binary)}),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return ""
    version = result.stdout.strip()
    if result.returncode != 0 or not version:
        return ""
    return version


def task(ctx: Context) -> TaskResult:
    """Install nodejs, npm and playwright-cli for the desktop user.

    The target state is reached when every configured apt package is
    installed and the playwright-cli binary answers --version as the
    desktop user; the task then returns changed=False. Force mode re-runs
    the npm install even when the binary already answers. A failed apt
    install or a failed npm install is an error TaskResult: the runner
    continues with the remaining tasks and never stops here.
    """

    cfg = ctx.config.playwright_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    changed = False
    messages: list[str] = []
    warnings: list[str] = []

    missing = [
        package
        for package in cfg.packages
        if not package_is_installed(package, cfg.package_status_timeout_seconds)
    ]
    if missing:
        _log("installing the playwright runtime packages")
        installed, failures, apt_warnings = install_packages(
            missing,
            install_timeout=timeout,
            update_timeout=timeout,
            retries=cfg.package_install_retries,
            skip_update=ctx.skip_apt_update,
        )
        warnings.extend(apt_warnings)
        if failures:
            detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
            return TaskResult(
                success=False,
                changed=changed,
                error=f"cannot install nodejs and npm: {detail}",
            )
        changed = True
        messages.append(f"installed {' and '.join(installed)}")

    installed_version = _cli_version(cfg, timeout=timeout)
    if installed_version and not force:
        message = f"already installed: playwright-cli {installed_version}"
        if warnings:
            message = f"{message}; {'; '.join(warnings)}"
        return TaskResult(success=True, changed=False, message=message)

    if installed_version:
        _log("reinstalling playwright-cli")
    else:
        _log(f"installing playwright-cli for {cfg.username}")
    try:
        run_command(
            _runuser_command(cfg)
            + substituted_command(
                cfg.npm_install_command,
                {
                    "cli_package": cfg.cli_package,
                    "prefix": str(_user_prefix(cfg)),
                },
            ),
            timeout=cfg.npm_install_timeout_seconds,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return TaskResult(
            success=False,
            changed=changed,
            error=f"playwright-cli install failed: {exc}",
        )
    changed = True
    messages.append(f"installed playwright-cli for {cfg.username}")

    after_version = _cli_version(cfg, timeout=timeout)
    if not after_version:
        return TaskResult(
            success=False,
            changed=changed,
            error="playwright-cli did not become available after the install",
        )
    binary = _cli_bin_path(cfg)
    message = f"playwright-cli {after_version} ready at {binary}"
    if messages:
        message = f"{'; '.join(messages)}; {message}"
    if warnings:
        message = f"{message}; {'; '.join(warnings)}"
    return TaskResult(success=True, changed=changed, message=message)
