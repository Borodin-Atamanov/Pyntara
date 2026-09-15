"""Task upnp_forwarding_setup: deploy the router port forwarding service.

The task deploys two systemd units: the oneshot service
upnp_forwarding.service, which runs the pyntara.upnp_forwarding module from
the shared deployment venv of system_metrics_setup with the single system
config as its only argument, and the timer upnp_forwarding.timer, which
runs that service after boot and then every configured interval, so the
rule on the router is re-asserted after a change of the address of the
machine or of the router (docs/spec/upnp-forwarding-setup.md). The timer
is enabled and started immediately and the service is run once as well, so
the forwarded rule exists at the end of a provisioning run and a broken
deployment shows in the install log instead of surfacing at the first
network change. Both rendered units carry the version of the deployed
code, taken from the deployed interpreter, so an update of that code
makes them differ from the units on the machine and the task writes them
again. The task is idempotent: it is done when both unit files
match their templates and the timer is enabled and active; force mode
rewrites the units and runs the service again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from string import Template

from pyntara import __version__, deployment
from pyntara.config import UpnpForwardingSetupConfig
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

# Module-level paths and helpers are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).


def _render_service_unit(
    cfg: UpnpForwardingSetupConfig,
    template_path: Path,
    venv_python: Path,
    system_config_path: Path,
    version: str,
) -> str:
    """Render the oneshot unit with its ExecStart line substituted.

    The service runs the deployment venv interpreter with the configured
    module and the configured system config path as its only argument; the
    line is fully expanded here, so the template carries no shell variables
    of its own. The version line names the deployed code this unit belongs
    to, so a unit on the machine that names another version is written
    again by the task below.
    """

    command = " ".join(
        substituted_command(
            cfg.module_run_command,
            {
                "python": str(venv_python),
                "module": cfg.service_module_name,
                "config_path": str(system_config_path),
            },
        )
    )
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        exec_lines=f"ExecStart={command}", version=version
    )


def _render_timer_unit(
    cfg: UpnpForwardingSetupConfig, template_path: Path, version: str
) -> str:
    """Render the timer unit with its bounds and its unit substituted.

    The first run happens after the boot delay, and every later run follows
    the previous one by the configured interval, so a network change is
    healed without a reboot and without a polling loop in the service. The
    version line is the mark of the deployment that wrote the timer, like
    the one of the service unit beside it.
    """

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        boot_delay_seconds=cfg.timer_boot_delay_seconds,
        interval_seconds=cfg.timer_interval_seconds,
        service_unit_name=cfg.service_unit_name,
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
        substituted_command(command, {"unit_name": service_name}),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0


def task(ctx: Context) -> TaskResult:
    """Deploy the units and the timer; skip when the goal is reached.

    The goal is reached when both unit files match their rendered templates
    and the timer is enabled and active, because the timer is what keeps the
    rule on the router alive. The rendered units carry the version of the
    deployed code, so an update of that code makes them stale and the
    rewrite below happens. Otherwise the units are written, systemd is
    reloaded, the timer is enabled and started, and the service runs once so
    the rule exists at the end of the run. Every step that cannot run is a
    warning of a completed task: a missing template skips the write of that
    unit alone, a failed write, reload, enable or start leaves the other
    steps, and a service that entered the failed state is reported while the
    deployment stays in place.
    """

    timeout = ctx.config.engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    cfg = ctx.config.upnp_forwarding_setup
    metrics = ctx.config.system_metrics_setup
    engine = ctx.config.engine
    venv_python = metrics.venv_dir / metrics.venv_python_relative_path
    unit_dir = engine.systemd_unit_dir
    data_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    warnings: list[str] = []
    version, version_warning = deployment.deployed_version(
        metrics.venv_version_command, venv_python, timeout, __version__
    )
    if version_warning is not None:
        warnings.append(version_warning)

    rendered: dict[str, str] = {}
    try:
        rendered[cfg.service_unit_name] = _render_service_unit(
            cfg,
            data_dir / cfg.service_template_file_name,
            venv_python,
            metrics.system_config_path,
            version,
        )
    except OSError as exc:
        warnings.append(f"cannot read the service template: {exc}")
    try:
        rendered[cfg.timer_unit_name] = _render_timer_unit(
            cfg, data_dir / cfg.timer_template_file_name, version
        )
    except OSError as exc:
        warnings.append(f"cannot read the timer template: {exc}")

    units_ok = True
    for name, content in rendered.items():
        matches = _unit_matches(unit_dir, name, content)
        units_ok = units_ok and matches
        _log(f"checking unit {name}: {'ok' if matches else 'missing or stale'}")
    timer_enabled = service_is_enabled(engine, cfg.timer_unit_name, timeout)
    timer_active = service_is_active(engine, cfg.timer_unit_name, timeout)
    _log(
        f"checking autorun {cfg.timer_unit_name}: "
        f"{'enabled' if timer_enabled else 'disabled'}"
    )
    _log(
        f"checking activity {cfg.timer_unit_name}: "
        f"{'active' if timer_active else 'inactive'}"
    )

    if not force and units_ok and timer_enabled and timer_active:
        _log("target state already reached, skipping")
        return TaskResult(
            success=True,
            changed=False,
            message=f"service {cfg.service_unit_name} configured",
            warnings=tuple(warnings),
        )

    changed = False
    written = False
    for name, content in rendered.items():
        if not force and _unit_matches(unit_dir, name, content):
            continue
        try:
            _write_unit(unit_dir, name, content)
        except OSError as exc:
            warnings.append(f"cannot write unit {name}: {exc}")
        else:
            _log(f"unit {name} written")
            written = True
            changed = True

    if written:
        try:
            run_command(
                substituted_command(cfg.systemctl_daemon_reload_command, {}),
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"cannot reload systemd: {exc}")

    if not timer_enabled or force:
        try:
            run_command(
                substituted_command(
                    cfg.systemctl_enable_command,
                    {"unit_name": cfg.timer_unit_name},
                ),
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"cannot enable {cfg.timer_unit_name}: {exc}")
        else:
            _log(f"timer {cfg.timer_unit_name} enabled")
            changed = True

    if not timer_active or force:
        try:
            run_command(
                substituted_command(
                    cfg.systemctl_start_command,
                    {"unit_name": cfg.timer_unit_name},
                ),
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"cannot start {cfg.timer_unit_name}: {exc}")
        else:
            _log(f"timer {cfg.timer_unit_name} started")
            changed = True

    try:
        run_command(
            substituted_command(
                cfg.systemctl_start_command,
                {"unit_name": cfg.service_unit_name},
            ),
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        warnings.append(f"cannot run {cfg.service_unit_name}: {exc}")
    else:
        _log(f"service {cfg.service_unit_name} started")
        changed = True
        try:
            failed = _service_is_failed(
                cfg.systemctl_is_failed_command, cfg.service_unit_name, timeout
            )
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(
                f"cannot check {cfg.service_unit_name}: {exc}"
            )
        else:
            if failed:
                warnings.append(
                    f"service {cfg.service_unit_name} entered the failed state"
                )

    message = f"service {cfg.service_unit_name} deployed"
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True,
        changed=changed,
        message=message,
        warnings=tuple(warnings),
    )
