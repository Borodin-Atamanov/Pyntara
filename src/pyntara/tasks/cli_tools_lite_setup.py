"""Task cli_tools_lite_setup: install the everyday console utility set.

The package list comes from pyntara.values.cli_tools_lite_setup.PACKAGES, and
the pair of package install values comes from the shared module
pyntara.values.common. The install itself is the shared path of
pyntara.package_set, which checks the real system state with dpkg-query,
installs only what is missing, refreshes the apt index once unless the run
skips it, and reports the installed share of the list
(docs/contracts/task-model.md). The media and document tools are the separate
section cli_tools_heavy_setup, so this section finishes quickly.

The section also owns the deferred command scheduler. at and batch are useful
only while the scheduler of the installed package is enabled and running, so
the task brings that service to that state after the install and reports a
scheduler it could not reach as a warning of the completed task.
"""

from __future__ import annotations

import subprocess

from pyntara.context import Context
from pyntara.logger import log_progress
from pyntara.models import TaskResult
from pyntara.package_set import install_package_set
from pyntara.systemd_unit import wait_service_active
from pyntara.utils import (
    package_is_installed,
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
)
from pyntara.values import cli_tools_lite_setup as lite_values
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names


def _ensure_deferred_scheduler_running(timeout: float) -> tuple[bool, list[str]]:
    """Bring the deferred command scheduler to enabled and running.

    The unit belongs to the package, so this step writes no unit file: it asks
    systemd for the boot state and for the runtime state and changes only what
    is not in the wanted state, which leaves a repeated run without a change.
    A step that fails is reported in warnings instead of raising, so the
    installed packages are kept and the run carries on. Returns (changed,
    warnings).
    """

    unit = lite_values.DEFERRED_COMMAND_SERVICE_UNIT_NAME
    changed = False
    warnings: list[str] = []
    if not service_is_enabled(unit, timeout):
        try:
            run_command(
                substituted_command(
                    engine_values.SYSTEMCTL_ENABLE_COMMAND, {"unit": unit}
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot enable {unit}: {exc}")
        else:
            log_progress(f"enabled {unit}")
            changed = True
    if not service_is_active(unit, timeout):
        try:
            run_command(
                substituted_command(
                    engine_values.SYSTEMCTL_START_COMMAND, {"unit": unit}
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot start {unit}: {exc}")
        else:
            log_progress(f"started {unit}")
            changed = True
            if not wait_service_active(
                unit,
                lite_values.START_CHECK_ATTEMPTS,
                lite_values.START_CHECK_RETRY_DELAY_SECONDS,
                timeout,
            ):
                warnings.append(f"{unit} did not report active after the start")
    return changed, warnings


def task(ctx: Context) -> TaskResult:
    """Install the everyday console utilities and start the deferred scheduler.

    The guard stands above every read of the values, and the install path
    itself, with the installed share and the warning for every package that
    could not be installed, lives in pyntara.package_set. The scheduler step
    follows the install, because its unit can only exist once its package is
    on the machine; the missing package is already named by the install
    shortfall, so a machine without it is left to that warning.
    """

    absent = missing_value_names(
        lite_values, lite_values.READ_VALUE_NAMES
    ) + missing_value_names(common_values, common_values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks.
        return TaskResult(
            success=True,
            message=(
                "the cli_tools_lite_setup values are not declared, "
                "nothing was changed"
            ),
            warnings=(
                "the cli_tools_lite_setup values are not declared: "
                + ", ".join(absent),
            ),
        )
    result = install_package_set(
        ctx,
        packages=lite_values.PACKAGES,
        threshold_percent=lite_values.PACKAGE_SUCCESS_THRESHOLD_PERCENT,
    )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    if not package_is_installed(lite_values.DEFERRED_COMMAND_PACKAGE_NAME, timeout):
        return result
    changed, warnings = _ensure_deferred_scheduler_running(timeout)
    message = result.message or ""
    if changed and not warnings:
        message = (
            f"{message}; the deferred command scheduler "
            f"{lite_values.DEFERRED_COMMAND_SERVICE_UNIT_NAME} is enabled and running"
        )
    return TaskResult(
        success=result.success,
        changed=result.changed or changed,
        message=message,
        warnings=(*result.warnings, *warnings),
    )
