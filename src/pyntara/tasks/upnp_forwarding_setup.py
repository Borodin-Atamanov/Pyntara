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
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.systemd_unit import (
    exec_start_line,
    service_is_failed,
    unit_matches,
    write_unit,
)
from pyntara.utils import (
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
    task_data_dir,
)
from pyntara.values import engine as engine_values
from pyntara.values import system_metrics_setup as metrics_values
from pyntara.values import upnp_forwarding_setup as values

# Module-level paths and helpers are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).


def _render_service_unit(
    template_path: Path,
    venv_python: Path,
    version: str,
) -> str:
    """Render the oneshot unit with its ExecStart line substituted.

    The service runs the deployment venv interpreter with the configured
    module and no argument, because every value it needs ships with the
    package; the line is fully expanded here, so the template carries no
    shell variables of its own. The version line names the deployed code this unit belongs
    to, so a unit on the machine that names another version is written
    again by the task below.
    """

    exec_line = exec_start_line(
        substituted_command(
            values.MODULE_RUN_COMMAND,
            {
                "python": str(venv_python),
                "module": values.SERVICE_MODULE_NAME,
            },
        )
    )
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(exec_lines=exec_line, version=version)


def _render_timer_unit(
    template_path: Path, version: str
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
        boot_delay_seconds=values.TIMER_BOOT_DELAY_SECONDS,
        interval_seconds=values.TIMER_INTERVAL_SECONDS,
        unit=values.SERVICE_UNIT_NAME,
        version=version,
    )


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

    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    venv_python = metrics_values.VENV_DIR / metrics_values.VENV_PYTHON_RELATIVE_PATH
    unit_dir = engine_values.SYSTEMD_UNIT_DIR
    data_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    warnings: list[str] = []
    version, version_warning = deployment.deployed_version(
        metrics_values.VENV_VERSION_COMMAND, venv_python, timeout, __version__
    )
    if version_warning is not None:
        warnings.append(version_warning)

    rendered: dict[str, str] = {}
    try:
        rendered[values.SERVICE_UNIT_NAME] = _render_service_unit(
            data_dir / values.SERVICE_TEMPLATE_FILE_NAME,
            venv_python,
            version,
        )
    except OSError as exc:
        warnings.append(f"cannot read the service template: {exc}")
    try:
        rendered[values.TIMER_UNIT_NAME] = _render_timer_unit(
            data_dir / values.TIMER_TEMPLATE_FILE_NAME, version
        )
    except OSError as exc:
        warnings.append(f"cannot read the timer template: {exc}")

    units_ok = True
    for name, content in rendered.items():
        matches = unit_matches(unit_dir, name, content)
        units_ok = units_ok and matches
        _log(f"checking unit {name}: {'ok' if matches else 'missing or stale'}")
    timer_enabled = service_is_enabled(values.TIMER_UNIT_NAME, timeout)
    timer_active = service_is_active(values.TIMER_UNIT_NAME, timeout)
    _log(
        f"checking autorun {values.TIMER_UNIT_NAME}: "
        f"{'enabled' if timer_enabled else 'disabled'}"
    )
    _log(
        f"checking activity {values.TIMER_UNIT_NAME}: "
        f"{'active' if timer_active else 'inactive'}"
    )

    if not force and units_ok and timer_enabled and timer_active:
        _log("target state already reached, skipping")
        return TaskResult(
            success=True,
            changed=False,
            message=f"service {values.SERVICE_UNIT_NAME} configured",
            warnings=tuple(warnings),
        )

    changed = False
    written = False
    for name, content in rendered.items():
        if not force and unit_matches(unit_dir, name, content):
            continue
        try:
            write_unit(unit_dir, name, content)
        except OSError as exc:
            warnings.append(f"cannot write unit {name}: {exc}")
        else:
            _log(f"unit {name} written")
            written = True
            changed = True

    if written:
        try:
            run_command(
                substituted_command(engine_values.SYSTEMCTL_DAEMON_RELOAD_COMMAND, {}),
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"cannot reload systemd: {exc}")

    if not timer_enabled or force:
        try:
            run_command(
                substituted_command(
                    engine_values.SYSTEMCTL_ENABLE_COMMAND,
                    {"unit": values.TIMER_UNIT_NAME},
                ),
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"cannot enable {values.TIMER_UNIT_NAME}: {exc}")
        else:
            _log(f"timer {values.TIMER_UNIT_NAME} enabled")
            changed = True

    if not timer_active or force:
        try:
            run_command(
                substituted_command(
                    engine_values.SYSTEMCTL_START_NO_BLOCK_COMMAND,
                    {"unit": values.TIMER_UNIT_NAME},
                ),
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"cannot start {values.TIMER_UNIT_NAME}: {exc}")
        else:
            _log(f"timer {values.TIMER_UNIT_NAME} started")
            changed = True

    try:
        run_command(
            substituted_command(
                engine_values.SYSTEMCTL_START_NO_BLOCK_COMMAND,
                {"unit": values.SERVICE_UNIT_NAME},
            ),
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        warnings.append(f"cannot run {values.SERVICE_UNIT_NAME}: {exc}")
    else:
        _log(f"service {values.SERVICE_UNIT_NAME} started")
        changed = True
        try:
            failed = service_is_failed(
                engine_values.SYSTEMCTL_IS_FAILED_COMMAND,
                {"unit": values.SERVICE_UNIT_NAME},
                timeout,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"cannot check {values.SERVICE_UNIT_NAME}: {exc}")
        else:
            if failed:
                warnings.append(
                    f"service {values.SERVICE_UNIT_NAME} entered the failed state"
                )

    message = f"service {values.SERVICE_UNIT_NAME} deployed"
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True,
        changed=changed,
        message=message,
        warnings=tuple(warnings),
    )
