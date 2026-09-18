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
the start, which is the intended no-op state, not a failure. The unit
carries the version of the deployed code, taken from the deployed
interpreter, so an update of that code makes the unit differ from the
one on the machine and the service is restarted with the new code. The
task is
idempotent: it skips when the unit file matches its source and the
service is enabled; force mode rewrites the unit and restarts the
service, which makes the service walk its port chain again.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from string import Template

from pyntara import __version__, deployment
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
    task_data_dir,
)
from pyntara.values import engine as engine_values
from pyntara.values import port_forwarding_setup as values

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).


def _render_service_unit(
    template_path: Path,
    venv_python: Path,
    module_name: str,
    system_config_path: Path,
    restart_seconds: int,
    version: str,
) -> str:
    """Render the service unit template with the ExecStart line substituted.

    The service runs the venv python with the declared port_forwarding module
    and the configured system config path as its only argument; the line is
    fully expanded here, so the template carries no shell variables of its
    own. The restart pause is a declared value, and the version line names the
    deployed code this unit belongs to: a unit on the machine that carries
    another version is a stale unit, so the task writes it again and restarts
    the service, and the code that runs is the code the unit was rendered for.
    """

    command = " ".join(
        substituted_command(
            values.MODULE_RUN_COMMAND,
            {
                "python": str(venv_python),
                "module": module_name,
                "config_path": str(system_config_path),
            },
        )
    )
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        exec_lines=f"ExecStart={command}",
        restart_seconds=restart_seconds,
        version=version,
    )


def _unit_matches(unit_dir: Path, name: str, expected: str) -> bool:
    """True when the deployed unit file equals the expected content."""

    unit_path = unit_dir / name
    try:
        if not unit_path.is_file():
            return False
        return unit_path.read_text(encoding="utf-8") == expected
    except OSError:
        return False


def _write_unit(unit_dir: Path, name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / name).write_text(content, encoding="utf-8")


def _service_is_failed(
    command: tuple[str, ...], service_name: str, timeout: float
) -> bool:
    """True when the systemd service is in the failed state."""

    result = run_command(
        substituted_command(command, {"service_unit_name": service_name}),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0


def _started_ok(
    service_name: str,
    timeout: float,
) -> bool:
    """True when the service either runs or exited cleanly.

    The service legitimately exits right after a start on a machine whose
    vault carries no port-forwarding data, so an inactive service is not
    a failure; only the failed state means the deployment broke. The loop
    checks for a bounded time: active or not-failed-after-the-grace ends
    as ok, failed ends as an error.
    """

    for _ in range(values.START_CHECK_ATTEMPTS):
        if service_is_active(service_name, timeout):
            return True
        if _service_is_failed(values.SYSTEMCTL_IS_FAILED_COMMAND, service_name, timeout):
            return False
        time.sleep(values.START_CHECK_RETRY_DELAY_SECONDS)
    return True


def task(ctx: Context) -> TaskResult:
    """Deploy the Auto Port Forwarding service; skip when the goal is reached.

    The goal is reached when the unit file matches the rendered template
    and the service is enabled; the service being active or cleanly exited
    depends on the machine vault content and is verified after a start.
    Otherwise the task writes the unit, reloads systemd, enables the
    service and starts it, and verifies that the started service is not
    in the failed state. The rendered unit names the version of the
    deployed code, so an update of that code makes the unit stale and the
    rewrite below restarts the service with the new code. Force mode
    rewrites the unit and restarts the
    service, so the tunnel walks its port chain again; the task never
    touches the port-forwarding state file, which the service writes and
    the telemetry reads. Every step that cannot
    run is a warning of a completed task: a missing template skips the
    unit write alone, a failed write, reload or enable leaves the other
    steps, and a service that entered the failed state is reported while
    the deployment stays in place.
    """

    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    metrics = ctx.config.system_metrics_setup
    venv_python = metrics.venv_dir / metrics.venv_python_relative_path
    system_config_path = metrics.system_config_path
    service_name = values.SERVICE_UNIT_NAME
    warnings: list[str] = []
    version, version_warning = deployment.deployed_version(
        metrics.venv_version_command, venv_python, timeout, __version__
    )
    if version_warning is not None:
        warnings.append(version_warning)

    try:
        unit: str | None = _render_service_unit(

            task_data_dir(ctx.repo_root, ctx.task_name) / values.SERVICE_TEMPLATE_FILE_NAME,
            venv_python,
            values.SERVICE_MODULE_NAME,
            system_config_path,
            values.SERVICE_RESTART_SECONDS,
            version,
        )
    except OSError as exc:
        # Without the rendered unit the unit file cannot be written; the
        # enable and the restart below still act on the unit that is
        # installed on the machine.
        warnings.append(f"cannot read the service template: {exc}")
        unit = None
    unit_dir = engine_values.SYSTEMD_UNIT_DIR
    unit_ok = unit is not None and _unit_matches(unit_dir, service_name, unit)
    _log(f"checking unit {service_name}: {'ok' if unit_ok else 'missing or stale'}")
    enabled = service_is_enabled(service_name, timeout)
    _log(f"checking autorun {service_name}: {'enabled' if enabled else 'disabled'}")
    active = service_is_active(service_name, timeout)
    _log(f"checking activity {service_name}: {'active' if active else 'inactive'}")

    if not force and unit_ok and enabled and active:
        _log("target state already reached, skipping")
        return TaskResult(
            success=True,
            changed=False,
            message=f"service {service_name} configured",
            warnings=tuple(warnings),
        )

    changed = False
    if not force and unit_ok and enabled and not active:
        # The service is deployed and enabled but not running: it exited
        # cleanly on an earlier start because the vault carried no
        # port-forwarding data. local_vault_setup runs before this task and
        # may have synced the data since, so a restart lets the service
        # re-read the vault and establish the tunnels.
        _log(
            f"service {service_name} deployed but inactive, "
            "restarting to read the vault"
        )
        changed = True
    if unit is not None and (not unit_ok or force):
        unit_written = False
        try:
            _write_unit(unit_dir, service_name, unit)
        except OSError as exc:
            warnings.append(f"cannot write unit {service_name}: {exc}")
        else:
            _log(f"unit {service_name} written")
            unit_written = True
            changed = True
        if unit_written:
            try:
                run_command(
                    substituted_command(values.SYSTEMCTL_DAEMON_RELOAD_COMMAND, {}),
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"cannot reload systemd: {exc}")

    if not enabled:
        enable_argv = substituted_command(
            values.SYSTEMCTL_ENABLE_COMMAND, {"service_unit_name": service_name}
        )
        try:
            run_command(enable_argv, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"cannot enable {service_name}: {exc}")
        else:
            _log(f"service {service_name} enabled")
            changed = True

    if force:
        # The forced action of this task is the restart below: the tunnel
        # then walks its port chain again. The port-forwarding state file
        # belongs to the service, which writes the port it accepted, so the
        # task never touches it.
        changed = True

    restart_argv = substituted_command(
        values.SYSTEMCTL_RESTART_COMMAND, {"service_unit_name": service_name}
    )
    try:
        run_command(restart_argv, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"cannot start {service_name}: {exc}")
    else:
        _log(f"service {service_name} started")
        if not _started_ok(service_name, timeout):
            warnings.append(
                f"service {service_name} entered the failed state after start"
            )
        else:
            _log(f"service {service_name} is running or cleanly exited")
    message = f"service {service_name} deployed"
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True,
        changed=changed,
        message=message,
        warnings=tuple(warnings),
    )
