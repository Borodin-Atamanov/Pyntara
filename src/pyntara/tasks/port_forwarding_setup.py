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
the start, which is the intended no-op state, not a failure. A service
that exited nonzero is neither active nor failed while systemd waits for
the next attempt, so the result of its last run is read as well and a
service that keeps restarting is a warning of the completed task, because
the machine then forwards nothing while the deployment looks done. The unit
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

from pyntara import __version__, deployment, metrics, port_forwarding
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
from pyntara.values import ssh_daemon_setup as ssh_daemon_values
from pyntara.values import system_metrics_setup as metrics_values

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).


def _render_service_unit(
    template_path: Path,
    venv_python: Path,
    module_name: str,
    restart_seconds: int,
    version: str,
) -> str:
    """Render the service unit template with the ExecStart line substituted.

    The service runs the venv python with the declared port_forwarding module
    and no argument, because every value it needs ships with the package; the
    line is fully expanded here, so the template carries no shell variables of
    its own. The restart pause is a declared value, and the version line names the
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


def _last_run_result(service_name: str, timeout: float) -> str:
    """The result word of the last run of the unit; "unknown" when unreadable.

    A service that exited nonzero makes systemd schedule the next attempt, and
    the unit is then neither active nor failed while that attempt is pending,
    so the start check cannot see the loop; the result of the last run is what
    names it (measured 2026-09-25: one unit restarted 64 times while the run
    reported a successful deployment).
    """

    try:
        result = run_command(
            substituted_command(
                values.SYSTEMCTL_SHOW_RESULT_COMMAND,
                {"service_unit_name": service_name},
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        _log(f"cannot read the result of the last run of {service_name}: {exc}")
        return "unknown"
    return result.stdout.strip().lower() or "unknown"


def _forwarding_readiness_warning() -> str | None:
    """The warning that names why the machine forwards nothing, or None.

    The service connects to the servers of the vault group when the passphrase
    of the machine vault decrypts the deployed key. A vault whose passphrase
    decrypts no deployed key (the default vault carries a freshly generated one
    by design) leaves the machine with a service that connects to nothing, and
    the run says so instead of reporting a deployment that works; the key
    check answers in a moment and never uses ssh-add, which keeps asking the
    askpass helper on a wrong passphrase. Nothing is reported when the vault
    cannot be read or carries no server group, because a machine without
    port-forwarding data has nothing to connect to by design.
    """

    kp = metrics.open_runtime_vault()
    if kp is None:
        return None
    servers = port_forwarding.read_server_addresses(kp, values.VAULT_GROUP_TITLE)
    passphrase = port_forwarding.read_passphrase(kp, values.PASSPHRASE_ENTRY_TITLE)
    key_path = (
        ssh_daemon_values.ROOT_SSH_DIR
        / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME
    )
    if not servers or not passphrase or not key_path.is_file():
        return None
    if port_forwarding.passphrase_decrypts_key(passphrase, key_path):
        return None
    return (
        f"the passphrase of the vault entry {values.PASSPHRASE_ENTRY_TITLE!r} does "
        f"not decrypt the port-forwarding key {key_path}, so the machine forwards "
        "no ports"
    )


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
    venv_python = metrics_values.VENV_DIR / metrics_values.VENV_PYTHON_RELATIVE_PATH
    service_name = values.SERVICE_UNIT_NAME
    warnings: list[str] = []
    version, version_warning = deployment.deployed_version(
        metrics_values.VENV_VERSION_COMMAND, venv_python, timeout, __version__
    )
    if version_warning is not None:
        warnings.append(version_warning)

    try:
        unit: str | None = _render_service_unit(

            task_data_dir(ctx.repo_root, ctx.task_name) / values.SERVICE_TEMPLATE_FILE_NAME,
            venv_python,
            values.SERVICE_MODULE_NAME,
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
    # The readiness of the machine belongs to both results: a unit that is
    # already in place still leaves the machine forwarding nothing when the
    # passphrase of its vault decrypts no deployed key.
    readiness_warning = _forwarding_readiness_warning()
    if readiness_warning is not None:
        warnings.append(readiness_warning)

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
            # A service that exited nonzero is neither active nor failed while
            # systemd waits for the next attempt, so the result of the last run
            # is read for the case it is not running; a running service is the
            # reached state and needs no question about an older run.
            if not service_is_active(service_name, timeout):
                last_result = _last_run_result(service_name, timeout)
                if last_result != values.SUCCESSFUL_SERVICE_RESULT:
                    warnings.append(
                        f"service {service_name} did not stay up after the start: "
                        f"its last run ended with {last_result}, the journal of the "
                        "unit names the reason"
                    )
                else:
                    _log(f"service {service_name} exited cleanly")
            else:
                _log(f"service {service_name} is running")
    message = f"service {service_name} deployed"
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True,
        changed=changed,
        message=message,
        warnings=tuple(warnings),
    )
