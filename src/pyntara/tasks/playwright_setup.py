"""Task playwright_setup: install the playwright-cli browser control tool.

The described goal is a playwright-cli available to the desktop user that
drives the Google Chrome installed by chrome_setup over its Chrome
DevTools Protocol listener on the loopback address. The task installs the
nodejs and npm packages from the Ubuntu archive (nodejs lives in the
universe component, enabled by add_extra_repos, a hard dependency of the
task) and then installs the npm package CLI_PACKAGE into the user prefix
HOME_DIR/USER_PREFIX_RELATIVE_PATH with NPM_INSTALL_COMMAND, running it as
the desktop user through RUNUSER_COMMAND. The binary lands at
CLI_BIN_RELATIVE_PATH inside that prefix, so no root-owned
npm prefix is used and the plain user can update it. The version is not
chased: npm installs the latest release, and a rerun whose apt packages are
installed and whose playwright-cli binary answers CLI_VERSION_COMMAND changes
nothing (docs/spec/playwright-setup.md). Force mode re-runs the npm install
regardless of the current binary. A value the values module does not declare
is reported as a warning and nothing is changed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    install_packages,
    package_is_installed,
    run_command,
    substituted_command,
)
from pyntara.values import missing_value_names
from pyntara.values import playwright_setup as playwright_values


def _user_prefix() -> Path:
    """The npm prefix under the desktop user home."""

    return Path(playwright_values.HOME_DIR) / (
        playwright_values.USER_PREFIX_RELATIVE_PATH
    )


def _cli_bin_path() -> Path:
    """The playwright-cli binary path inside the user prefix."""

    return _user_prefix() / playwright_values.CLI_BIN_RELATIVE_PATH


def _runuser_command() -> list[str]:
    """The runuser prefix that runs a command as the desktop user."""

    return substituted_command(
        playwright_values.RUNUSER_COMMAND,
        {
            "username": playwright_values.USERNAME,
            "home_dir": playwright_values.HOME_DIR,
        },
    )


def _cli_version(*, timeout: float) -> str:
    """The playwright-cli version as the desktop user, or an empty string.

    The probe runs through runuser because the binary lives in the user
    prefix and node reads the user npm cache under HOME. A binary that is
    missing, not executable, or fails to report a version yields an empty
    string, which stands for not installed.
    """

    binary = _cli_bin_path()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return ""
    try:
        result = run_command(
            _runuser_command()
            + substituted_command(
                playwright_values.CLI_VERSION_COMMAND,
                {"cli_bin": str(binary)},
            ),
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
    install or a failed npm install never stops the run: the failure is
    reported in warnings and the task completes, because the remaining
    tasks of the run do not depend on this tool (architecture contract,
    Task contract). Only the missing runtime packages stop the further
    steps of this task, since without them npm cannot install anything.
    """

    absent = missing_value_names(
        playwright_values, playwright_values.READ_VALUE_NAMES
    )
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks.
        return TaskResult(
            success=True,
            message="the playwright values are not declared, nothing was changed",
            warnings=(
                "the playwright values are not declared: " + ", ".join(absent),
            ),
        )
    engine = ctx.config.engine
    timeout = engine.command_timeout_seconds
    status_timeout = playwright_values.PACKAGE_STATUS_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    changed = False
    messages: list[str] = []
    warnings: list[str] = []

    missing = [
        package
        for package in playwright_values.PACKAGES
        if not package_is_installed(engine, package, status_timeout)
    ]
    if missing:
        _log("installing the playwright runtime packages")
        installed, failures, apt_warnings = install_packages(
            engine,
            missing,
            install_timeout=timeout,
            update_timeout=timeout,
            retries=playwright_values.PACKAGE_INSTALL_RETRIES,
            skip_update=ctx.skip_apt_update,
        )
        warnings.extend(apt_warnings)
        if failures:
            detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
            warnings.append(f"cannot install nodejs and npm: {detail}")
            return TaskResult(
                success=True,
                changed=changed,
                message=f"playwright-cli not installed: {detail}",
                warnings=tuple(warnings),
            )
        changed = True
        messages.append(f"installed {' and '.join(installed)}")

    installed_version = _cli_version(timeout=timeout)
    if installed_version and not force:
        message = f"already installed: playwright-cli {installed_version}"
        if warnings:
            message = f"{message}; {'; '.join(warnings)}"
        return TaskResult(success=True, changed=False, message=message)

    if installed_version:
        _log("reinstalling playwright-cli")
    else:
        _log(f"installing playwright-cli for {playwright_values.USERNAME}")
    try:
        run_command(
            _runuser_command()
            + substituted_command(
                playwright_values.NPM_INSTALL_COMMAND,
                {
                    "cli_package": playwright_values.CLI_PACKAGE,
                    "prefix": str(_user_prefix()),
                },
            ),
            timeout=playwright_values.NPM_INSTALL_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"playwright-cli install failed: {exc}")
        return TaskResult(
            success=True,
            changed=changed,
            message=f"playwright-cli not installed for {playwright_values.USERNAME}",
            warnings=tuple(warnings),
        )
    changed = True
    messages.append(f"installed playwright-cli for {playwright_values.USERNAME}")

    after_version = _cli_version(timeout=timeout)
    if not after_version:
        warnings.append(
            "playwright-cli did not become available after the install"
        )
        message = f"playwright-cli not available at {_cli_bin_path()}"
        if messages:
            message = f"{'; '.join(messages)}; {message}"
        return TaskResult(
            success=True,
            changed=changed,
            message=message,
            warnings=tuple(warnings),
        )
    binary = _cli_bin_path()
    message = f"playwright-cli {after_version} ready at {binary}"
    if messages:
        message = f"{'; '.join(messages)}; {message}"
    if warnings:
        message = f"{message}; {'; '.join(warnings)}"
    return TaskResult(success=True, changed=changed, message=message)
