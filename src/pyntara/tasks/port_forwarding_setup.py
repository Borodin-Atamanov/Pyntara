"""Task port_forwarding_setup: deploy the Auto Port Forwarding service.

The task deploys the systemd unit auto_port_forwarding.service that
starts the port-forwarding service from the shared deployment venv of
system_metrics_setup with the single system config as its only argument
(docs/spec/port-forwarding-setup.md). The port-forwarding key pair
itself is deployed by the ssh_daemon_setup task together with the
restricted authorized_keys line; this task only configures the service,
so a machine is ready to forward as soon as its vault carries the
server group and the passphrase. The unit is enabled and started
immediately, so a broken deployment fails the task and shows in the
install log instead of surfacing at the first reboot; on a vault
without the port-forwarding data the service exits cleanly right after
the start, which is the intended no-op state, not a failure. The task is
idempotent: it skips when the unit file matches its source and the
service is enabled; force mode rewrites the unit and restarts the
service.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    run_command,
    service_is_active,
    service_is_enabled,
)

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = (
    REPO_ROOT / "task_data" / "port_forwarding_setup" / "auto_port_forwarding.service"
)
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")


def _render_service_unit(
    venv_python: Path,
    system_config_path: Path,
    journal_identifier: str,
    restart_seconds: int,
) -> str:
    """Render the service unit template with the ExecStart line substituted.

    The service runs the venv python with the port_forwarding module and
    the configured system config path as its only argument; the line is
    fully expanded here, so the template carries no shell variables of
    its own. The journal identifier and the restart pause come from the
    config.
    """

    command = " ".join(
        [
            str(venv_python),
            "-m",
            "pyntara.port_forwarding",
            str(system_config_path),
        ]
    )
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        exec_lines=f"ExecStart={command}",
        journal_identifier=journal_identifier,
        restart_seconds=restart_seconds,
    )


def _unit_matches(name: str, expected: str) -> bool:
    """True when the deployed unit file equals the expected content."""

    unit_path = SYSTEMD_UNIT_DIR / name
    try:
        if not unit_path.is_file():
            return False
        return unit_path.read_text(encoding="utf-8") == expected
    except OSError:
        return False


def _write_unit(name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (SYSTEMD_UNIT_DIR / name).write_text(content, encoding="utf-8")


def _service_is_failed(service_name: str, timeout: float) -> bool:
    """True when the systemd service is in the failed state."""

    result = run_command(
        ["systemctl", "is-failed", service_name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0


def _started_ok(
    service_name: str,
    attempts: int,
    retry_delay_seconds: float,
    timeout: float,
) -> bool:
    """True when the service either runs or exited cleanly.

    The service legitimately exits right after a start on a machine whose
    vault carries no port-forwarding data, so an inactive service is not
    a failure; only the failed state means the deployment broke. The loop
    checks for a bounded time: active or not-failed-after-the-grace ends
    as ok, failed ends as an error.
    """

    for _ in range(attempts):
        if service_is_active(service_name, timeout):
            return True
        if _service_is_failed(service_name, timeout):
            return False
        time.sleep(retry_delay_seconds)
    return True


def task(ctx: Context) -> TaskResult:
    """Deploy the Auto Port Forwarding service; skip when the goal is reached.

    The goal is reached when the unit file matches the rendered template
    and the service is enabled; the service being active or cleanly exited
    depends on the machine vault content and is verified after a start.
    Otherwise the task writes the unit, reloads systemd, enables the
    service and starts it, and verifies that the started service is not
    in the failed state. A missing template is a broken deployment and an
    error.
    """

    timeout = ctx.config.engine.command_timeout_seconds
    force = "port_forwarding_setup" in ctx.force_tasks
    pf = ctx.config.port_forwarding_setup
    metrics = ctx.config.system_metrics_setup
    venv_python = metrics.venv_dir / "bin" / "python"
    system_config_path = metrics.system_config_path
    service_name = pf.service_unit_name

    try:
        unit = _render_service_unit(
            venv_python, system_config_path, pf.journal_identifier, pf.service_restart_seconds
        )
    except OSError as exc:
        return TaskResult(
            success=False,
            error=f"cannot read the service template: {exc}",
        )
    unit_ok = _unit_matches(service_name, unit)
    _log(f"checking unit {service_name}: {'ok' if unit_ok else 'missing or stale'}")
    enabled = service_is_enabled(service_name, timeout)
    _log(f"checking autorun {service_name}: {'enabled' if enabled else 'disabled'}")

    if not force and unit_ok and enabled:
        _log("target state already reached, skipping")
        return TaskResult(
            success=True, changed=False, message=f"service {service_name} configured"
        )

    changed = False
    if not unit_ok or force:
        try:
            _write_unit(service_name, unit)
        except OSError as exc:
            return TaskResult(
                success=False, changed=changed, error=f"cannot write unit {service_name}: {exc}"
            )
        _log(f"unit {service_name} written")
        changed = True
        try:
            run_command(["systemctl", "daemon-reload"], timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False, changed=changed, error=f"cannot reload systemd: {exc}"
            )

    if not enabled:
        try:
            run_command(["systemctl", "enable", service_name], timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False, changed=changed, error=f"cannot enable {service_name}: {exc}"
            )
        _log(f"service {service_name} enabled")
        changed = True

    try:
        run_command(["systemctl", "restart", service_name], timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(
            success=False,
            changed=changed,
            error=f"cannot start {service_name}: {exc}",
        )
    _log(f"service {service_name} started")
    if not _started_ok(service_name, 10, 1.0, timeout):
        return TaskResult(
            success=False,
            changed=changed,
            error=f"service {service_name} entered the failed state after start",
        )
    _log(f"service {service_name} is running or cleanly exited")
    return TaskResult(
        success=True,
        changed=changed,
        message=f"service {service_name} deployed",
    )
