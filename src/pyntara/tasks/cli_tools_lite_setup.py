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

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.package_set import install_package_set
from pyntara.values import cli_tools_lite_setup as lite_values
from pyntara.values import common as common_values
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
    return install_package_set(
        ctx,
        packages=lite_values.PACKAGES,
        threshold_percent=lite_values.PACKAGE_SUCCESS_THRESHOLD_PERCENT,
    )
