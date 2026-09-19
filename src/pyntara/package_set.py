"""Shared install path of a declared package set.

Six sections install a list of packages from the Ubuntu archive the same way:
they ask dpkg which packages of the list are not in the installed state,
install exactly those through the shared apt helper of utils, and report what
happened. The wiring of the shared values (the command timeout, the install
retries and the apt index refresh the run may skip) stood in every one of those
sections, so it lives here once, together with the single wording of a failed
package.

install_package_set is the whole step for a section whose work is the package
set itself: it returns the finished TaskResult with the installed share of the
list, and a share below the configured threshold is reported as a warning of a
completed task. A section that continues with other work after the packages
calls install_missing_packages instead and keeps its own report, and a section
that must phrase a failure itself reads the reasons through failure_detail.

A section still reads its own values: it passes its declared package list and
its threshold to these functions, so the values stay declared, read and tested
in the values package of that section and never here.
"""

from __future__ import annotations

from collections.abc import Sequence

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values


def missing_packages(packages: Sequence[str]) -> list[str]:
    """The packages of the list dpkg does not see as fully installed.

    The query distinguishes "install ok installed" from leftovers like
    "deinstall ok config-files", so a package that was removed but left its
    configuration is reinstalled instead of being trusted.
    """

    status_timeout = common_values.PACKAGE_STATUS_TIMEOUT_SECONDS
    return [
        package
        for package in packages
        if not package_is_installed(package, status_timeout)
    ]


def install_missing_packages(
    ctx: Context, packages: Sequence[str]
) -> tuple[list[str], list[str], list[tuple[str, str]], list[str]]:
    """Install the missing packages of the list.

    Returns (missing, installed, failures, warnings): the packages that were
    not installed before the call, the ones this call installed, the ones that
    still failed with their reason, and the non-fatal problems such as a failed
    apt index refresh. The index is refreshed once before the first install
    unless the run asked to skip it, and every package gets one attempt plus
    the configured retries, so a package that cannot be installed never blocks
    the others. Nothing is done and nothing is logged when the target state is
    already reached.
    """

    missing = missing_packages(packages)
    if not missing:
        return [], [], [], []
    _log(f"installing: {', '.join(missing)}")
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    installed, failures, warnings = install_packages(
        missing,
        install_timeout=timeout,
        update_timeout=timeout,
        retries=common_values.PACKAGE_INSTALL_RETRIES,
        skip_update=ctx.skip_apt_update,
    )
    return missing, installed, failures, warnings


def failure_detail(failures: Sequence[tuple[str, str]]) -> str:
    """One line naming every failed package with the reason apt gave.

    A section that phrases its own failure reads the reasons through this
    function, so the failed name and its reason are written the same way in
    every report of the run.
    """

    return "; ".join(f"{name}: {reason}" for name, reason in failures)


def install_package_set(
    ctx: Context, *, packages: Sequence[str], threshold_percent: int
) -> TaskResult:
    """Install the declared package set and report the installed share.

    The share is the number of packages of the list in the installed state
    after the run divided by the length of the list, counted with the declared
    percent scale. A share below threshold_percent is the finding of a
    completed task, and every package that could not be installed is a warning
    of its own with its reason, so the failing names reach the closing line of
    the run either way. The message names the installed packages, the total
    count and the share. A list that is already complete changes nothing and
    reports that the goal was reached.
    """

    missing, installed, failures, warnings = install_missing_packages(ctx, packages)
    if not missing:
        return TaskResult(success=True, changed=False, message="already installed")
    percent_scale = engine_values.PERCENT_SCALE
    installed_total = len(packages) - len(missing) + len(installed)
    installed_percent = installed_total * percent_scale // len(packages)
    already_installed = [
        package for package in packages if package not in missing
    ]
    installed_summary = (
        f"installed {installed_total}/{len(packages)} "
        f"({installed_percent}%): {', '.join(already_installed + installed) or 'none'}"
    )
    failure_warnings = tuple(
        f"{name}: {reason}" for name, reason in failures
    )
    if installed_percent < threshold_percent:
        shortfall = (
            f"installed share {installed_percent}% is below the configured "
            f"{threshold_percent}%"
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
        message=(f"{installed_summary}; threshold {threshold_percent}%"),
        warnings=(*warnings, *failure_warnings),
    )
