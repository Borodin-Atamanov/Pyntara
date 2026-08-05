"""Task cli_tools: install the console utility set.

The package list comes from config.toml through ctx.config.cli_tools.packages
(architecture contract section 3). The task checks the real system state
with dpkg-query and installs only what is missing, so repeated runs change
nothing (docs/contracts/task-model.md). Packages are installed one by one:
a missing or uninstallable package must not block the others, and the task
reports a partial success instead of failing the whole run (resilience rule,
architecture contract section 7). The apt index is refreshed once before
the first install, so packages resolve from a fresh index; the refresh is
skipped when ctx.skip_apt_update is True (test or offline runs).
"""

from __future__ import annotations

import subprocess

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import run_command

# apt must never ask questions; all package operations run noninteractive.
APT_EXTRA_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


def _is_installed(package: str, timeout: float) -> bool:
    """True when dpkg considers the package fully installed.

    The status query distinguishes "install ok installed" from leftovers
    like "deinstall ok config-files", so an uninstalled package is never
    treated as installed. The timeout comes from config.toml.
    """

    result = run_command(
        ["dpkg-query", "-W", "-f=${Status}", package],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def _install_once(package: str, timeout: float) -> tuple[bool, str]:
    """Install one package; return (success, error_text)."""

    try:
        run_command(
            ["apt-get", "install", "-y", package],
            extra_env=APT_EXTRA_ENV,
            timeout=timeout,
        )
        return True, ""
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _install_packages(
    packages: list[str],
    *,
    install_timeout: float,
    update_timeout: float,
    retries: int,
    skip_update: bool,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Install each package individually; return (installed, failures).

    The apt index is refreshed once before the first install, so packages
    resolve from a fresh index; skip_update=True disables the refresh for
    test or offline runs. A failing refresh is recorded and the install is
    still attempted, because the package may already be in the local cache.
    A failing package is recorded and never blocks the others; each package
    gets one initial attempt plus `retries` retries.
    """

    installed: list[str] = []
    failures: list[tuple[str, str]] = []
    if not skip_update:
        try:
            run_command(
                ["apt-get", "update"],
                extra_env=APT_EXTRA_ENV,
                timeout=update_timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            failures.append(("apt index refresh", str(exc)))
    for package in packages:
        ok = False
        error = ""
        for _ in range(retries + 1):
            ok, error = _install_once(package, install_timeout)
            if ok:
                break
        if ok:
            installed.append(package)
        else:
            failures.append((package, error))
    return installed, failures


def task(ctx: Context) -> TaskResult:
    """Install the console utility set; skip when the goal is already reached.

    A package that cannot be installed is reported, not fatal: the task
    succeeds when at least one missing package was installed, and every
    failure is listed in the message. Timeouts and the retry count come
    from config.toml through Context; the apt index refresh can be skipped
    through ctx.skip_apt_update.
    """

    cli = ctx.config.cli_tools
    missing = [
        package
        for package in cli.packages
        if not _is_installed(package, cli.package_status_timeout_seconds)
    ]
    if not missing:
        return TaskResult(success=True, changed=False, message="already installed")
    installed, failures = _install_packages(
        missing,
        install_timeout=ctx.config.engine.command_timeout_seconds,
        update_timeout=ctx.config.engine.command_timeout_seconds,
        retries=cli.package_install_retries,
        skip_update=ctx.skip_apt_update,
    )
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
