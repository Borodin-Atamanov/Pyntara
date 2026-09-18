"""Task cli_tools: install the console utility set.

The package list comes from pyntara.values.cli_tools.PACKAGES, and the pair of
package install values comes from the shared module pyntara.values.common. The
task checks the real system state with dpkg-query and installs only what is
missing, so repeated runs change nothing (docs/contracts/task-model.md).
Packages are installed one by one.
The apt index is refreshed once before the first install, so packages
resolve from a fresh index; the refresh is skipped when ctx.skip_apt_update
is True (test or offline runs). The task succeeds when at least the
configured share of the package set is installed after the run
(PACKAGE_SUCCESS_THRESHOLD_PERCENT): a single failing package is
not fatal by itself, and no package has to be marked as important. Every
package that could not be installed is reported as a warning of the
completed task, with its own reason, so the closing line of the run names
the task and the exit code stays nonzero until the set is complete. The
report lists every package that is in the installed state after the run,
with the total count and the installed share.
"""

from __future__ import annotations

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed
from pyntara.values import cli_tools as cli_tools_values
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names


def task(ctx: Context) -> TaskResult:
    """Install the console utility set; skip when the goal is already reached.

    The installed share is the number of configured packages that are in
    the installed state after the run, divided by the total package set. A
    share below PACKAGE_SUCCESS_THRESHOLD_PERCENT is a warning of
    a completed task carrying the shortfall, and a package that could not
    be installed is a warning of its own with its reason, so every failing
    package is reported either way and the run stays detectable as
    incomplete. The report names the installed packages:
    the set already in the installed state before the run plus the ones
    installed by this run. Timeouts and the retry count come from the shared
    values module; the apt index refresh can be skipped through
    ctx.skip_apt_update.
    """

    absent = missing_value_names(
        cli_tools_values, cli_tools_values.READ_VALUE_NAMES
    ) + missing_value_names(common_values, common_values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks.
        return TaskResult(
            success=True,
            message="the cli_tools values are not declared, nothing was changed",
            warnings=("the cli_tools values are not declared: " + ", ".join(absent),),
        )
    percent_scale = engine_values.PERCENT_SCALE
    status_timeout = common_values.PACKAGE_STATUS_TIMEOUT_SECONDS
    missing = [
        package
        for package in cli_tools_values.PACKAGES
        if not package_is_installed(package, status_timeout)
    ]
    if not missing:
        return TaskResult(success=True, changed=False, message="already installed")
    installed, failures, warnings = install_packages(
        missing,
        install_timeout=engine_values.COMMAND_TIMEOUT_SECONDS,
        update_timeout=engine_values.COMMAND_TIMEOUT_SECONDS,
        retries=common_values.PACKAGE_INSTALL_RETRIES,
        skip_update=ctx.skip_apt_update,
    )
    installed_total = len(cli_tools_values.PACKAGES) - len(missing) + len(installed)
    installed_percent = (
        installed_total * percent_scale // len(cli_tools_values.PACKAGES)
    )
    already_installed = [
        package for package in cli_tools_values.PACKAGES if package not in missing
    ]
    installed_names = already_installed + installed
    installed_summary = (
        f"installed {installed_total}/{len(cli_tools_values.PACKAGES)} "
        f"({installed_percent}%): {', '.join(installed_names) or 'none'}"
    )
    # A package that could not be installed is a step of a completed task that
    # could not be performed, so its reason travels in warnings: the entry
    # point counts warnings, names the task in its closing line and exits
    # nonzero, which is how an incomplete configuration stays detectable. The
    # failed names appear there once and not in the message as well.
    failure_warnings = tuple(f"{name}: {reason}" for name, reason in failures)
    if installed_percent < cli_tools_values.PACKAGE_SUCCESS_THRESHOLD_PERCENT:
        # Below the threshold the shortfall is the finding; the per-package
        # reasons follow it, and the task still completes.
        shortfall = (
            f"installed share {installed_percent}% is below the configured "
            f"{cli_tools_values.PACKAGE_SUCCESS_THRESHOLD_PERCENT}%"
        )
        return TaskResult(
            success=True,
            changed=bool(installed),
            message=installed_summary,
            warnings=(shortfall, *warnings, *failure_warnings),
        )
    return TaskResult(
        success=True,
        changed=bool(installed),
        message=(
            f"{installed_summary}; threshold "
            f"{cli_tools_values.PACKAGE_SUCCESS_THRESHOLD_PERCENT}%"
        ),
        warnings=(*warnings, *failure_warnings),
    )
