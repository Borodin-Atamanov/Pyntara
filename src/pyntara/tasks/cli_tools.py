"""Task cli_tools: install the console utility set.

The package list comes from config.toml through ctx.config.cli_tools.packages
(architecture contract section 3). The task checks the real system state
with dpkg-query and installs only what is missing, so repeated runs change
nothing (docs/contracts/task-model.md). Packages are installed one by one:
a missing or uninstallable package must not block the others, and the task
reports a partial success instead of failing the whole run (resilience rule,
architecture contract section 7).
"""

from __future__ import annotations

import subprocess

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import run_command

# apt must never ask questions; all package operations run noninteractive.
APT_EXTRA_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


def _is_installed(package: str) -> bool:
    """True when dpkg considers the package fully installed.

    The status query distinguishes "install ok installed" from leftovers
    like "deinstall ok config-files", so an uninstalled package is never
    treated as installed.
    """

    result = run_command(
        ["dpkg-query", "-W", "-f=${Status}", package],
        check=False,
        capture=True,
        timeout=30,
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def _install_once(package: str) -> tuple[bool, str]:
    """Install one package; return (success, error_text)."""

    try:
        run_command(
            ["apt-get", "install", "-y", package],
            extra_env=APT_EXTRA_ENV,
        )
        return True, ""
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _install_packages(packages: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Install each package individually; return (installed, failures).

    A failing package is recorded and never blocks the others. The apt
    index is refreshed once, on the first install failure; a refresh that
    itself fails is recorded and the install is still retried once, because
    the package may already be in the local cache.
    """

    installed: list[str] = []
    failures: list[tuple[str, str]] = []
    index_refreshed = False
    for package in packages:
        ok, _ = _install_once(package)
        if ok:
            installed.append(package)
            continue
        if not index_refreshed:
            try:
                run_command(["apt-get", "update"], extra_env=APT_EXTRA_ENV)
                index_refreshed = True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                failures.append(("apt index refresh", str(exc)))
        ok, error = _install_once(package)
        if ok:
            installed.append(package)
        else:
            failures.append((package, error))
    return installed, failures


def task(ctx: Context) -> TaskResult:
    """Install the console utility set; skip when the goal is already reached.

    A package that cannot be installed is reported, not fatal: the task
    succeeds when at least one missing package was installed, and every
    failure is listed in the message.
    """

    missing = [
        package
        for package in ctx.config.cli_tools.packages
        if not _is_installed(package)
    ]
    if not missing:
        return TaskResult(success=True, changed=False, message="already installed")
    installed, failures = _install_packages(missing)
    if not installed:
        detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
        return TaskResult(success=False, changed=False, error=detail)
    message = f"installed: {', '.join(installed)}"
    if failures:
        failed_names = ", ".join(name for name, _ in failures)
        message = f"{message}; failed: {failed_names}"
        detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
        return TaskResult(
            success=True,
            changed=True,
            message=message,
            error=detail,
        )
    return TaskResult(success=True, changed=True, message=message)
