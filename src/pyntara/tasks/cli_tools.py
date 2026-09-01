"""Task cli_tools: install the console utility set.

The package list comes from config.toml through ctx.config.cli_tools.packages
(architecture contract, Configuration). The task checks the real system state
with dpkg-query and installs only what is missing, so repeated runs change
nothing (docs/contracts/task-model.md). Packages are installed one by one.
The apt index is refreshed once before the first install, so packages
resolve from a fresh index; the refresh is skipped when ctx.skip_apt_update
is True (test or offline runs). The task succeeds when at least the
configured share of the package set is installed after the run
(cli_tools.package_success_threshold_percent): a single failing package is
not fatal by itself, and no package has to be marked as important. The
report lists every package that is in the installed state after the run,
with the total count and the installed share.
"""

from __future__ import annotations

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed


def task(ctx: Context) -> TaskResult:
    """Install the console utility set; skip when the goal is already reached.

    The installed share is the number of configured packages that are in
    the installed state after the run, divided by the total package set. A
    share below cli_tools.package_success_threshold_percent is a fatal
    error; at or above it the task succeeds and every failing package is
    reported. The report names the installed packages: the set already in
    the installed state before the run plus the ones installed by this run.
    Timeouts and the retry count come from config.toml through Context; the
    apt index refresh can be skipped through ctx.skip_apt_update.
    """

    cli = ctx.config.cli_tools
    missing = [
        package
        for package in cli.packages
        if not package_is_installed(package, cli.package_status_timeout_seconds)
    ]
    if not missing:
        return TaskResult(success=True, changed=False, message="already installed")
    installed, failures, warnings = install_packages(
        missing,
        install_timeout=ctx.config.engine.command_timeout_seconds,
        update_timeout=ctx.config.engine.command_timeout_seconds,
        retries=cli.package_install_retries,
        skip_update=ctx.skip_apt_update,
    )
    installed_total = len(cli.packages) - len(missing) + len(installed)
    installed_percent = installed_total * 100 // len(cli.packages)
    already_installed = [
        package for package in cli.packages if package not in missing
    ]
    installed_names = already_installed + installed
    installed_summary = (
        f"installed {installed_total}/{len(cli.packages)} "
        f"({installed_percent}%): {', '.join(installed_names) or 'none'}"
    )
    failed_detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
    if installed_percent < cli.package_success_threshold_percent:
        detail = installed_summary
        if failed_detail:
            detail = f"{detail}; failed: {failed_detail}"
        if warnings:
            detail = f"{detail}; {'; '.join(warnings)}"
        return TaskResult(success=False, changed=bool(installed), error=detail)
    message = (
        f"{installed_summary}; threshold "
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
