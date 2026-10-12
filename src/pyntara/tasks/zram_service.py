"""Task zram_service: deploy the ZRAM program and its boot service.

The ZRAM devices belong to the program the section ships
(task_data/zram_service/configure_zram.py). That program reads the installed
memory and the CPU core count at every run, computes the device count and the
size of every device, brings the devices of the machine to that state and
activates them; the boot service runs the very same program at every start, so
the run and the boot apply one code and cannot drift apart. This is what makes
the machine follow a memory size or a core count that changed between two
boots, instead of repeating numbers that were measured once when the task ran.

The task deploys the program, renders the unit template with the command line it
builds from the values module, enables the service and runs the program once,
so the work of this run is done by the code the machine will run at its next
boot. Kernel 7.0 creates one zram device on every read of the hot_add attribute
and older kernels create one device per write; the program tells the two apart
at run time, so the unit no longer carries the interface that was detected when
the task ran.

The program prints one line per step and its last line carries the result as a
JSON object of changed, skipped_reason, error and warnings; that object is the
only thing this task parses, while the sentences go into the run log as they
are. A step the program could not perform is reported here as a warning of a
completed task: the machine stays usable with the devices it could configure
instead of the run failing.

Every step is idempotent: the program is written only when its content differs
from the deployed one, the unit only when the rendered content differs, and the
service is enabled only when it is not enabled yet. Both files are written
through a temporary file that replaces the target in one step, because a machine
that loses power in the middle of an in-place write would be left with a
truncated program or unit and the next boot would fail on it. After the program
has run, the unit itself is started, so the artifact the next boot uses is
proved by this run instead of being trusted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult, warned_result
from pyntara.package_set import failure_detail, install_missing_packages
from pyntara.systemd_unit import (
    deploy_program,
    install_and_enable_unit,
    parse_program_result,
)
from pyntara.utils import (
    run_command,
    substituted_command,
    task_data_dir,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import zram_service as values


def _program_command(*, force: bool) -> tuple[str, ...]:
    """The command line of the deployed program, built from the values.

    The option names are the interface of the program, which parses exactly
    these names and reads no other source; the end to end test of the section
    runs the program with this command line, so a name that drifts on one side
    fails the tests instead of failing on a target machine.
    """

    command = [
        str(values.PROGRAM_DEPLOY_PATH),
        "--meminfo",
        str(common_values.MEMINFO_PATH),
        "--meminfo-total-key",
        common_values.MEMINFO_TOTAL_KEY,
        "--cpuinfo",
        str(values.CPUINFO_PATH),
        "--cpuinfo-processor-key",
        values.CPUINFO_PROCESSOR_KEY,
        "--compressor",
        values.COMPRESSOR,
        "--swap-priority",
        str(values.SWAP_PRIORITY),
        "--memory-fraction-percent",
        str(values.MEMORY_FRACTION_PERCENT),
        "--percent-scale",
        str(engine_values.PERCENT_SCALE),
        "--fallback-cpu-count",
        str(values.FALLBACK_CPU_COUNT),
        "--alignment-bytes",
        str(values.ALIGNMENT_BYTES),
        "--bytes-per-kib",
        str(engine_values.BYTES_PER_KIB),
        "--module-name",
        values.MODULE_NAME,
        "--reset-busy-attempts",
        str(values.RESET_BUSY_ATTEMPTS),
        "--reset-busy-retry-delay-seconds",
        str(values.RESET_BUSY_RETRY_DELAY_SECONDS),
        "--command-timeout-seconds",
        str(engine_values.COMMAND_TIMEOUT_SECONDS),
    ]
    if force:
        command.append("--force")
    return tuple(command)


def _render_unit(template_path: Path, command: tuple[str, ...]) -> str:
    """Render the unit template with the command line of the program."""

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(exec_lines=f"ExecStart={' '.join(command)}")


def _reported_warnings(outcome: dict[str, object]) -> list[str]:
    """The warnings the program reported, each as its own sentence."""

    reported = outcome.get("warnings")
    if not isinstance(reported, list):
        return []
    return [str(item) for item in reported if str(item)]


def task(ctx: Context) -> TaskResult:
    """Deploy the ZRAM program and its boot service; the program does the work.

    The goal is reached when the program is deployed, the unit carries the
    rendered command line, the service is enabled and the program reports the
    target state of the devices as reached; the task then returns changed=False.
    Otherwise it deploys what differs, enables the service, runs the program
    once and reports what the program did. A step that cannot run is reported as
    a warning of a completed task, so the runner continues with the remaining
    tasks and never stops here.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES) + missing_value_names(
        common_values, common_values.READ_VALUE_NAMES
    )
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on. The
        # guard stands above every read.
        return TaskResult(
            success=True,
            message="the zram_service values are not declared, nothing was changed",
            warnings=(
                "the zram_service values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    service_name = values.SERVICE_UNIT_NAME
    data_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    warnings: list[str] = []
    changed = False

    _, installed, failures, package_warnings = install_missing_packages(
        ctx, values.PACKAGES
    )
    warnings.extend(package_warnings)
    if installed:
        _log(f"installed for the zram tools: {', '.join(installed)}")
    if failures:
        warnings.append(f"packages of the zram tools: {failure_detail(failures)}")

    command = _program_command(force=force)

    _log(f"deploying the zram program to {values.PROGRAM_DEPLOY_PATH}")
    program_changed, program_error = deploy_program(
        data_dir / values.PROGRAM_FILE_NAME,
        values.PROGRAM_DEPLOY_PATH,
        values.PROGRAM_FILE_MODE,
    )
    if program_error is not None:
        # Without the program neither this run nor a boot can configure the
        # devices, so the task reports the reason and leaves the rest of the
        # machine alone instead of installing a service that cannot start.
        warnings.append(program_error)
        return warned_result(
            changed=False,
            message="the zram program could not be deployed",
            warnings=warnings,
        )
    changed = changed or program_changed

    template_path = data_dir / values.UNIT_TEMPLATE_FILE_NAME
    _log(f"rendering the unit template {template_path}")
    content: str | None = None
    try:
        content = _render_unit(template_path, command)
    except OSError as exc:
        warnings.append(f"cannot read the unit template: {exc}")
    unit_ready = False
    if content is not None:
        unit_ready, unit_extra_changed, unit_warnings = install_and_enable_unit(
            engine_values.SYSTEMD_UNIT_DIR,
            service_name,
            content,
            timeout=timeout,
            reload_command=values.SYSTEMCTL_DAEMON_RELOAD_COMMAND,
            enable_command=values.SYSTEMCTL_ENABLE_COMMAND,
            enable_placeholder="service_unit_name",
        )
        changed = changed or unit_extra_changed
        warnings.extend(unit_warnings)

    try:
        result = run_command(list(command), check=False, capture=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"the zram program could not run: {exc}")
    else:
        for line in result.stdout.splitlines():
            _log(line)
        outcome = parse_program_result(result.stdout)
        if outcome is None:
            warnings.append(
                "the zram program reported nothing readable, "
                f"exit code {result.returncode}"
            )
        else:
            error_text = outcome.get("error")
            skipped_reason = outcome.get("skipped_reason")
            if isinstance(error_text, str) and error_text:
                warnings.append(f"the zram program failed: {error_text}")
            elif isinstance(skipped_reason, str) and skipped_reason:
                warnings.append(f"no zram device was configured: {skipped_reason}")
            warnings.extend(_reported_warnings(outcome))
            if outcome.get("changed") is True:
                changed = True

    if unit_ready:
        # The unit is the artifact the next boot runs, so it is started here as
        # well: a unit that cannot start is a finding of this run instead of a
        # surprise of the next boot. The program already did the work above, so
        # this start changes nothing.
        try:
            run_command(
                substituted_command(
                    values.SYSTEMCTL_START_COMMAND,
                    {"service_unit_name": service_name},
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"the boot service could not be started: {exc}")

    if changed:
        message = f"zram devices and service {service_name} configured"
    else:
        message = "already configured"
    return warned_result(changed=changed, message=message, warnings=warnings)
