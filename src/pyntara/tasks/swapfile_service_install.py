"""Task swapfile_service_install: deploy the swap program and its boot service.

The swap file itself belongs to the program the section ships
(task_data/swapfile_service_install/configure_swapfile.py). That program reads
the installed memory and the free space, computes the size, creates the file,
formats it, activates it and refuses storage that keeps its data in memory; the
boot service runs the very same program at every start, so the run and the boot
apply one code and cannot drift apart. The task deploys the program, renders the
unit template with the command line it builds from the values module, enables
the service and runs the program once, so the work of this run is done by the
code the machine will run at its next boot.

The program prints one line per step and its last line carries the result as a
JSON object of changed, skipped_reason and error; that object is the only thing
this task parses, while the sentences go into the run log as they are. A storage
that keeps its data in memory is refused by the program and reported here as a
warning of a completed task: the machine stays usable without a disk swap file
instead of the run failing, and the service stays installed, because the storage
of the next boot may well be a disk.

Every step is idempotent: the program is written only when its content differs
from the deployed one, the unit only when the rendered content differs, and the
service is enabled only when it is not enabled yet.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    run_command,
    service_is_enabled,
    substituted_command,
    task_data_dir,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import swapfile_service_install as values


def _number_text(value: float) -> str:
    """A size factor the way the command line carries it: 2 instead of 2.0."""

    return str(int(value)) if float(value).is_integer() else str(value)


def _program_command(*, force: bool) -> tuple[str, ...]:
    """The command line of the deployed program, built from the values.

    The option names are the interface of the program, which parses exactly
    these names and reads no other source; the end to end test of the section
    runs the program with this command line, so a name that drifts on one side
    fails the tests instead of failing on a target machine.
    """

    command = [
        str(values.PROGRAM_DEPLOY_PATH),
        "--swapfile",
        str(values.SWAPFILE_PATH),
        "--file-mode",
        f"{values.SWAPFILE_MODE:o}",
        "--ram-multiplier",
        _number_text(values.RAM_MULTIPLIER),
        "--ram-extra-mb",
        str(values.RAM_EXTRA_MB),
        "--disk-fraction",
        _number_text(values.DISK_FRACTION),
        "--size-tolerance-mb",
        str(values.SIZE_TOLERANCE_MB),
        "--probe-size-kb",
        str(values.PROBE_SIZE_KB),
        "--meminfo",
        str(values.MEMINFO_PATH),
        "--meminfo-total-key",
        common_values.MEMINFO_TOTAL_KEY,
        "--command-timeout-seconds",
        str(engine_values.COMMAND_TIMEOUT_SECONDS),
    ]
    if force:
        command.append("--force")
    return tuple(command)


def _deploy_program(source: Path, target: Path) -> tuple[bool, str | None]:
    """Put the program at its deployed path with the declared mode.

    The file is written only when its content differs, so a rerun leaves the
    deployed program alone and reports no change of its own.
    """

    try:
        content = source.read_bytes()
    except OSError as exc:
        return False, f"cannot read the program {source}: {exc}"
    try:
        if target.is_file() and target.read_bytes() == content:
            _log(f"program already deployed: {target}")
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(values.PROGRAM_FILE_MODE)
    except OSError as exc:
        return False, f"cannot deploy the program to {target}: {exc}"
    _log(f"program deployed: {target}")
    return True, None


def _render_unit(
    template_path: Path, command: tuple[str, ...], swapfile_path: Path
) -> str:
    """Render the unit template with the command line and the swapfile path."""

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        exec_lines=f"ExecStart={' '.join(command)}",
        swapfile_path=str(swapfile_path),
    )


def _write_unit_file(
    unit_dir: Path, service_name: str, content: str
) -> tuple[bool, str | None]:
    """Write the unit file when it differs from the rendered content."""

    path = unit_dir / service_name
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            _log(f"unit file already current: {path}")
            return False, None
        unit_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write the unit file {path}: {exc}"
    _log(f"unit file written: {path}")
    return True, None


def _parse_program_result(stdout: str) -> dict[str, object] | None:
    """The result object the program prints as its last line, or None.

    The program answers with one JSON object; anything else means the caller
    cannot know what happened, and the task then reports that instead of
    guessing from the sentences.
    """

    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            parsed: object = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
        return None
    return None


def _result(*, changed: bool, message: str, warnings: list[str]) -> TaskResult:
    """Build the result of the task, carrying the warning of a skipped step."""

    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def task(ctx: Context) -> TaskResult:
    """Deploy the swap program and its boot service; the program does the work.

    The goal is reached when the program is deployed, the unit carries the
    rendered command line, the service is enabled and the program reports the
    target state of the swap file as reached; the task then returns
    changed=False. Otherwise it deploys what differs, enables the service,
    runs the program once and reports what the program did. A step that cannot
    run is reported as a warning of a completed task, so the runner continues
    with the remaining tasks and never stops here.
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
            message=(
                "the swapfile_service_install values are not declared, "
                "nothing was changed"
            ),
            warnings=(
                "the swapfile_service_install values are not declared: "
                + ", ".join(absent),
            ),
        )
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks
    service_name = values.SERVICE_UNIT_NAME
    data_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    command = _program_command(force=force)
    warnings: list[str] = []
    changed = False

    _log(f"deploying the swap program to {values.PROGRAM_DEPLOY_PATH}")
    program_changed, program_error = _deploy_program(
        data_dir / values.PROGRAM_FILE_NAME, values.PROGRAM_DEPLOY_PATH
    )
    if program_error is not None:
        # Without the program neither this run nor a boot can set the swap up,
        # so the task reports the reason and leaves the rest of the machine
        # alone instead of installing a service that cannot start.
        warnings.append(program_error)
        return _result(
            changed=False,
            message="the swap program could not be deployed",
            warnings=warnings,
        )
    changed = changed or program_changed

    template_path = data_dir / values.UNIT_TEMPLATE_FILE_NAME
    _log(f"rendering the unit template {template_path}")
    content: str | None = None
    try:
        content = _render_unit(template_path, command, values.SWAPFILE_PATH)
    except OSError as exc:
        warnings.append(f"cannot read the unit template: {exc}")
    if content is not None:
        unit_changed, unit_error = _write_unit_file(
            engine_values.SYSTEMD_UNIT_DIR, service_name, content
        )
        if unit_error is not None:
            warnings.append(unit_error)
        elif unit_changed:
            changed = True
            try:
                _log("reloading systemd: systemctl daemon-reload")
                run_command(
                    list(values.SYSTEMCTL_DAEMON_RELOAD_COMMAND), timeout=timeout
                )
                _log("systemd reloaded")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"systemd reload failed: {exc}")
        enabled = service_is_enabled(service_name, timeout)
        _log(
            f"checking autorun service {service_name}: "
            f"{'enabled' if enabled else 'disabled'}"
        )
        if not enabled:
            _log(f"enabling service: systemctl enable {service_name}")
            try:
                run_command(
                    substituted_command(
                        values.SYSTEMCTL_ENABLE_COMMAND,
                        {"service_unit_name": service_name},
                    ),
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"systemd enable failed: {exc}")
            else:
                _log("service enabled")
                changed = True

    _log(f"running the swap program: {' '.join(command)}")
    try:
        result = run_command(list(command), check=False, capture=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"the swap program could not run: {exc}")
    else:
        for line in result.stdout.splitlines():
            _log(line)
        outcome = _parse_program_result(result.stdout)
        if outcome is None:
            warnings.append(
                "the swap program reported nothing readable, "
                f"exit code {result.returncode}"
            )
        else:
            error_text = outcome.get("error")
            skipped_reason = outcome.get("skipped_reason")
            if isinstance(error_text, str) and error_text:
                warnings.append(f"the swap program failed: {error_text}")
            elif isinstance(skipped_reason, str) and skipped_reason:
                warnings.append(f"no swap file was created: {skipped_reason}")
            if outcome.get("changed") is True:
                changed = True

    if changed:
        message = (
            f"swapfile {values.SWAPFILE_PATH} and service {service_name} configured"
        )
    else:
        message = "already configured"
    return _result(changed=changed, message=message, warnings=warnings)
