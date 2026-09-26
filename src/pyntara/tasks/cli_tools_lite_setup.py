"""Task cli_tools_lite_setup: install the everyday console utility set.

The package list comes from pyntara.values.cli_tools_lite_setup.PACKAGES, and
the pair of package install values comes from the shared module
pyntara.values.common. The install itself is the shared path of
pyntara.package_set, which checks the real system state with dpkg-query,
installs only what is missing, refreshes the apt index once unless the run
skips it, and reports the installed share of the list
(docs/contracts/task-model.md). The media and document tools are the separate
section cli_tools_heavy_setup, so this section finishes quickly.
"""

from __future__ import annotations

import subprocess

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.package_set import install_package_set
from pyntara.utils import run_command, service_is_active, substituted_command
from pyntara.values import cli_tools_lite_setup as lite_values
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names


def task(ctx: Context) -> TaskResult:
    """Install the everyday console utilities; skip when the goal is reached.

    The guard stands above every read of the values, and the install path
    itself, with the installed share and the warning for every package that
    could not be installed, lives in pyntara.package_set.
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
    warnings = list(result.warnings)
    changed = result.changed
    if _ensure_atd_service(
        timeout=engine_values.PROCESS_CHECK_TIMEOUT_SECONDS, warnings=warnings
    ):
        changed = True
    return TaskResult(
        success=result.success,
        changed=changed,
        message=result.message,
        warnings=tuple(warnings),
    )


def _ensure_atd_service(*, timeout: float, warnings: list[str]) -> bool:
    """Enable and start the atd service; True when this step started it.

    The batch command is a front end of that service, so a machine that has
    the at package but leaves the service down accepts a batch request and
    never runs it. The service is enabled first, which also starts it after a
    reboot, and its state is read back. Every failure of the calls, including
    a state query that cannot answer, is a warning of a completed task and
    never a failure of the run.
    """

    try:
        if service_is_active(lite_values.ATD_UNIT_NAME, timeout):
            _log(f"{lite_values.ATD_UNIT_NAME} is already active")
            return False
        run_command(
            substituted_command(
                lite_values.ENABLE_AND_START_SERVICE_COMMAND,
                {"unit": lite_values.ATD_UNIT_NAME},
            ),
            timeout=timeout,
        )
        if not service_is_active(lite_values.ATD_UNIT_NAME, timeout):
            warnings.append(
                f"{lite_values.ATD_UNIT_NAME} was enabled but does not run, so "
                "the batch command does nothing"
            )
            return False
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"cannot enable and start {lite_values.ATD_UNIT_NAME}: {exc}")
        return False
    _log(f"enabled and started {lite_values.ATD_UNIT_NAME}")
    return True
