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
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
# The systemd unit directory is a fixed machine contract (architecture
# contract, Configuration), shared with the system_metrics_setup task.
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")


def task(ctx: Context) -> TaskResult:
    """Start the collector service once; skip when the unit is not deployed.

    The unit name comes from the config through Context: the collector
    service unit of the system_metrics_setup section. When the unit file is
    absent, the deployment did not happen and the task skips with
    changed=False. Otherwise the service is started non-blocking through
    the shared run_command with the engine timeout; a failed start returns
    an error TaskResult so the runner reports it.
    """

    service_name = ctx.config.system_metrics_setup.collector.service_unit_name
    unit_path = SYSTEMD_UNIT_DIR / service_name
    if not unit_path.is_file():
        _log(f"collector unit {unit_path} not deployed, skipping")
        return TaskResult(
            success=True,
            changed=False,
            message="collector unit not deployed",
        )
    _log(f"starting collector once: systemctl start --no-block {service_name}")
    try:
        run_command(
            ["systemctl", "start", "--no-block", service_name],
            timeout=ctx.config.engine.command_timeout_seconds,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(
            success=False,
            changed=True,
            error=f"cannot start collector service {service_name}: {exc}",
        )
    _log(f"collector service {service_name} started")
    return TaskResult(
        success=True,
        changed=True,
        message=f"collector service {service_name} started",
    )
