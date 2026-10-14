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
    exec_start_line,
    install_and_enable_unit,
    run_program_and_read_outcome,
    start_deployed_unit,
)
from pyntara.utils import (
    apt_package_record_command,
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
    return template.substitute(exec_lines=exec_start_line(command))


def _kernel_release() -> str | None:
    """The release of the running kernel, or None when it was not read."""

    try:
        return values.KERNEL_RELEASE_FILE_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _log(f"cannot read {values.KERNEL_RELEASE_FILE_PATH}: {exc}")
        return None


def _module_is_available(module_name: str, timeout: float) -> bool:
    """True when the kernel already carries the module or can load it.

    A module that was loaded already has its directory under the kernel module
    tree, and a module built into the kernel has one as well, because the kernel
    registers it there; the probe asks the loader about the remaining case
    without loading anything. The program of this section loads the module the
    same way, so an answered probe is the promise that the program will find it.
    """

    if (values.MODULE_SYSFS_DIR / module_name).exists():
        return True
    command = substituted_command(
        values.MODULE_PROBE_COMMAND, {"module_name": module_name}
    )
    try:
        result = run_command(command, check=False, capture=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        _log(f"cannot ask the module loader about {module_name}: {exc}")
        return False
    return result.returncode == 0


def _archive_knows_package(package: str, timeout: float) -> bool:
    """True when the archive of this machine carries a package name."""

    try:
        result = run_command(
            apt_package_record_command(package),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        _log(f"cannot ask the archive about {package}: {exc}")
        return False
    return result.returncode == 0


def _ensure_module(
    ctx: Context, module_name: str, timeout: float
) -> tuple[bool, list[str]]:
    """Make the kernel module loadable; the answer and the warnings.

    The module belongs to the installed kernel and not to this section, so the
    probe of the loader stands first and a machine that already answers it is
    told nothing. A kernel whose module tree lacks the module is completed by the
    package that carries it, and that package is named by the distribution
    release: the declared candidate names are asked of the archive and the ones
    this release knows are installed, so a name that exists nowhere is never
    installed and never reported as a failure of its own.

    A module that no candidate brings is reported as a warning naming the machine
    fact, because the rest of the section works without it: the machine keeps the
    disk swap and the devices it has, the run does not stop here, and the person
    reading the log learns which kernel has to be installed instead.
    """

    if _module_is_available(module_name, timeout):
        _log(f"the kernel module {module_name} is available")
        return True, []
    release = _kernel_release()
    if release is None:
        return False, [
            (
                f"the kernel module {module_name} is not available and the release "
                f"of the running kernel was not read from "
                f"{values.KERNEL_RELEASE_FILE_PATH}"
            )
        ]
    candidates = [
        template.format(kernel_release=release)
        for template in values.MODULE_CARRIER_PACKAGE_NAME_TEMPLATES
    ]
    known = [name for name in candidates if _archive_knows_package(name, timeout)]
    warnings: list[str] = []
    if known:
        _log(f"installing the carrier of the {module_name} module: {', '.join(known)}")
        _, installed, failures, package_warnings = install_missing_packages(ctx, known)
        warnings.extend(package_warnings)
        if installed:
            _log(f"installed the carrier of the module: {', '.join(installed)}")
        if failures:
            warnings.append(
                f"the carrier of the {module_name} module: {failure_detail(failures)}"
            )
    if _module_is_available(module_name, timeout):
        _log(f"the kernel module {module_name} is available after the install")
        return True, warnings
    reason = (
        "were installed without it"
        if known
        else "are unknown to the archive of this machine"
    )
    warnings.append(
        f"the kernel module {module_name} is not available on kernel {release}: the "
        f"packages {', '.join(candidates)} {reason}, so this kernel carries no such "
        "module and the devices of this section cannot be created; a kernel whose "
        "module tree is complete provides it"
    )
    return False, warnings


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

    _, module_warnings = _ensure_module(ctx, values.MODULE_NAME, timeout)
    warnings.extend(module_warnings)

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
            reload_command=engine_values.SYSTEMCTL_DAEMON_RELOAD_COMMAND,
            enable_command=engine_values.SYSTEMCTL_ENABLE_COMMAND,
        )
        changed = changed or unit_extra_changed
        warnings.extend(unit_warnings)

    _, program_changed, program_warnings = run_program_and_read_outcome(
        command,
        program_label="the zram program",
        skipped_reason_sentence="no zram device was configured",
        timeout=timeout,
    )
    changed = changed or program_changed
    warnings.extend(program_warnings)

    if unit_ready:
        # The unit is the artifact the next boot runs, so it is started here as
        # well: a unit that cannot start is a finding of this run instead of a
        # surprise of the next boot. The program already did the work above, so
        # this start changes nothing.
        warnings.extend(
            start_deployed_unit(
                service_name,
                start_command=engine_values.SYSTEMCTL_START_COMMAND,
                timeout=timeout,
            )
        )

    if changed:
        message = f"zram devices and service {service_name} configured"
    else:
        message = "already configured"
    return warned_result(changed=changed, message=message, warnings=warnings)
