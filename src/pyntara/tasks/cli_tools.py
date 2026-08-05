"""Task cli_tools: install the console utility set.

The package list comes from config.toml through ctx.config.cli_tools.packages
(architecture contract section 3). The task checks the real system state
with dpkg-query and installs only what is missing, so repeated runs change
nothing (docs/contracts/task-model.md). Packages are installed one by one.
The apt index is refreshed once before the first install, so packages
resolve from a fresh index; the refresh is skipped when ctx.skip_apt_update
is True (test or offline runs). The task succeeds when at least the
configured share of the package set is installed after the run
(cli_tools.package_success_threshold_percent): a single failing package is
not fatal by itself, and no package has to be marked as important.
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
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """Install each package individually; return (installed, failures, warnings).

    failures is a list of (name, reason); warnings carries non-fatal
    problems such as a failed apt index refresh. The apt index is refreshed
    once before the first install, so packages resolve from a fresh index;
    skip_update=True disables the refresh for test or offline runs. Each
    package gets one initial attempt plus `retries` retries; a package that
    still fails is recorded and never blocks the others.
    """

    installed: list[str] = []
    failures: list[tuple[str, str]] = []
    warnings: list[str] = []
    if not skip_update:
        try:
            run_command(
                ["apt-get", "update"],
                extra_env=APT_EXTRA_ENV,
                timeout=update_timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"apt index refresh: {exc}")
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
    return installed, failures, warnings


def task(ctx: Context) -> TaskResult:
    """Install the console utility set; skip when the goal is already reached.

    The installed share is the number of configured packages that are in
    the installed state after the run, divided by the total package set. A
    share below cli_tools.package_success_threshold_percent is a fatal
    error; at or above it the task succeeds and every failing package is
    reported. Timeouts and the retry count come from config.toml through
    Context; the apt index refresh can be skipped through ctx.skip_apt_update.
    """

    cli = ctx.config.cli_tools
    missing = [
        package
        for package in cli.packages
        if not _is_installed(package, cli.package_status_timeout_seconds)
    ]
    if not missing:
        return TaskResult(success=True, changed=False, message="already installed")
    installed, failures, warnings = _install_packages(
        missing,
        install_timeout=ctx.config.engine.command_timeout_seconds,
        update_timeout=ctx.config.engine.command_timeout_seconds,
        retries=cli.package_install_retries,
        skip_update=ctx.skip_apt_update,
    )
    installed_total = len(cli.packages) - len(missing) + len(installed)
    installed_percent = installed_total * 100 // len(cli.packages)
    failed_detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
    if installed_percent < cli.package_success_threshold_percent:
        detail = failed_detail
        if warnings:
            detail = f"{detail}; {'; '.join(warnings)}"
        return TaskResult(success=False, changed=bool(installed), error=detail)
    message = (
        f"installed {installed_total}/{len(cli.packages)} "
        f"({installed_percent}%), threshold "
        f"{cli.package_success_threshold_percent}%"
    )
    if failures:
        failed_names = ", ".join(name for name, _ in failures)
        message = f"{message}; failed: {failed_names}"
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True,
        changed=bool(installed),
        message=message,
        error=failed_detail if failures else None,
    )
