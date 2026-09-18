"""Task system_metrics_initial_collect: start the collector once after install.

The System Metrics collector service is deployed by the system_metrics_setup
task and normally starts only through its timer after the first boot
(docs/spec/system-metrics.md, section Report collector). The task runs
right before the final commit_final_system_metrics task of the catalog
and starts the already installed collector service once,
so the network report reaches the queue right after provisioning instead of
waiting for the boot run. The start is non-blocking (systemctl start
--no-block): the collector may wait up to its retry window inside the
service, and the installer must not block on it. The task depends on
system_metrics_setup and reads the service unit name from the config through
Context; when the unit file is missing, the deployment did not happen and
the task skips. A failed start is an error: the install log must show it
(no silent failures).
"""

from __future__ import annotations

import subprocess

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command, substituted_command
from pyntara.values import engine as engine_values


def task(ctx: Context) -> TaskResult:
    """Start the collector service once; skip when the unit is not deployed.

    The unit name and the command come from the config through Context: the
    collector service unit of the system_metrics_setup section and the
    start command of the collector table, with {service_unit_name}
    substituted. When the unit file is absent, the deployment did not
    happen and the task skips with changed=False. Otherwise the service is
    started through the shared run_command with the engine timeout; a
    failed start is a warning of a completed task, so the run continues
    and the collector retries through its own restart policy.
    """

    collector = ctx.config.system_metrics_setup.collector
    service_name = collector.service_unit_name
    unit_path = engine_values.SYSTEMD_UNIT_DIR / service_name
    if not unit_path.is_file():
        _log(f"collector unit {unit_path} not deployed, skipping")
        return TaskResult(
            success=True,
            changed=False,
            message="collector unit not deployed",
        )
    start_argv = substituted_command(
        collector.start_command, {"service_unit_name": service_name}
    )
    _log(f"starting collector once: {' '.join(start_argv)}")
    try:
        run_command(
            start_argv,
            timeout=engine_values.COMMAND_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warning = f"cannot start collector service {service_name}: {exc}"
        return TaskResult(
            success=True,
            changed=False,
            message=warning,
            warnings=(warning,),
        )
    _log(f"collector service {service_name} started")
    return TaskResult(
        success=True,
        changed=True,
        message=f"collector service {service_name} started",
    )
