"""Task zswap_service: configure the zswap compressed swap cache.

Zswap stores pages that are in the process of being swapped out in a
compressed RAM pool before they reach the backing swapfile, trading CPU
cycles for reduced swap I/O. The task writes the configured parameters
into the kernel attribute directory and installs a systemd oneshot service
that repeats the same writes at every boot. Kernel 7.0 (Kubuntu 26.04)
exposes exactly five parameters: enabled, compressor, max_pool_percent,
accept_threshold_percent and shrinker_enabled; the zpool and
same_filled_pages_enabled attributes no longer exist because zsmalloc is
the only pool and same-filled page handling is always on. The unit file
is rendered from the template at task_data/zswap_service/zswap.service
with the ExecStart block substituted (string.Template); the service never
reads the values itself. The task is idempotent: it skips when every
parameter already equals the value and the service is enabled; force mode
rewrites all parameters.
"""

from __future__ import annotations

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
from pyntara.values import missing_value_names
from pyntara.values import zswap_service as values


def _parameter_paths() -> dict[str, Path]:
    """The kernel attribute file of every configured parameter."""

    return {
        name: values.PARAMETERS_DIR_PATH / name
        for name, _ in values.PARAMETER_VALUES
    }


def _target_values() -> dict[str, str]:
    """Target values keyed by parameter name, in write order.

    The value of a parameter is the text the kernel is given: the Y or N
    spelling a boolean attribute reports, the digits or the word of a
    number and of a string, so the rendered unit, the idempotency
    comparison and the read-back verification all share one representation.
    """

    return dict(values.PARAMETER_VALUES)


def _normalize(expected: str, value: str) -> str:
    """Canonical spelling of one read-back value.

    A boolean attribute is reported by the kernel as Y or N but also
    accepts 1 and 0; the expected value decides whether the attribute is a
    boolean, so the comparison maps every accepted spelling to the
    canonical Y/N form and never depends on kernel quirks.
    """

    if expected in ("Y", "N"):
        return "Y" if value.upper() in ("Y", "1") else "N"
    return value


def _read_value(path: Path) -> str | None:
    """Current value of one zswap parameter, stripped, or None when absent."""

    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _write_sysfs(path: Path, value: str) -> None:
    """Write one value into a sysfs attribute file.

    Raises OSError when the attribute does not exist or the kernel rejects
    the value.
    """

    path.write_text(value, encoding="utf-8")


def _render_unit(
    template_path: Path,
    target: dict[str, str],
    paths: dict[str, Path],
) -> str:
    """Render the service unit template with the ExecStart block substituted.

    One ExecStart line per parameter writes the exact configured value, so
    the boot service reproduces the install-time configuration. The line
    template comes from the values module, with the value and the attribute
    path as its placeholders. The block is fully expanded here, so the
    template carries no shell variables of its own and substitute cannot
    trip on stray dollar signs.
    """

    lines = [
        values.UNIT_EXEC_LINE_TEMPLATE.format(value=value, path=paths[name])
        for name, value in target.items()
    ]
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(exec_lines="\n".join(lines))


def _write_unit_file(unit_dir: Path, service_name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / service_name).write_text(content, encoding="utf-8")


def task(ctx: Context) -> TaskResult:
    """Write the zswap parameters and install the boot service; skip when done.

    The goal is reached when every parameter in /sys/module/zswap/parameters
    already equals the configured value and the service is enabled; the task
    then returns changed=False. Otherwise it writes the mismatching
    parameters (all of them in force mode), verifies them by reading back,
    writes the unit file and enables the service. Every step is reported to
    stdout: measurements and decisions as single lines that include their
    result, long-running commands as a line before and a line after. A step
    that could not be performed never stops the others: the failure is
    reported in warnings and the task completes (architecture contract, Task
    contract).
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks.
        return TaskResult(
            success=True,
            message="the zswap_service values are not declared, nothing was changed",
            warnings=(
                "the zswap_service values are not declared: " + ", ".join(absent),
            ),
        )
    timeout = ctx.config.engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    parameter_names = tuple(name for name, _ in values.PARAMETER_VALUES)
    service_name = values.SERVICE_UNIT_NAME
    target = _target_values()
    paths = _parameter_paths()
    warnings: list[str] = []

    current: dict[str, str | None] = {}
    for name in parameter_names:
        value = _read_value(paths[name])
        current[name] = value
        shown = "absent" if value is None else value
        _log(f"reading {paths[name]}: {shown}")

    mismatches: list[str] = []
    for name in parameter_names:
        value = current[name]
        if value is None or _normalize(target[name], value) != target[name]:
            mismatches.append(name)

    enabled = service_is_enabled(ctx.config.engine, service_name, timeout)
    _log(
        f"checking autorun service {service_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )

    if not force and not mismatches and enabled:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    for name in parameter_names:
        if force or name in mismatches:
            _log(f"writing {paths[name]}: {target[name]}")
            try:
                _write_sysfs(paths[name], target[name])
            except OSError as exc:
                warnings.append(f"cannot write {name}: {exc}")
            else:
                changed = True

    if changed:
        _log("verifying zswap parameters")
        problems: list[str] = []
        for name in parameter_names:
            value = _read_value(paths[name])
            if value is None or _normalize(target[name], value) != target[name]:
                problems.append(f"{name} mismatch")
        if problems:
            warnings.append(f"parameters not applied: {'; '.join(problems)}")
        else:
            _log("verification passed")

    if not enabled:
        changed = True

    template_path = (
        task_data_dir(ctx.repo_root, ctx.task_name)
        / values.UNIT_TEMPLATE_FILE_NAME
    )
    _log(f"rendering unit template from {template_path}")
    try:
        content = _render_unit(template_path, target, paths)
    except OSError as exc:
        warnings.append(f"cannot read unit template: {exc}")
    else:
        unit_dir = ctx.config.engine.systemd_unit_dir
        _log(f"writing unit file {unit_dir / service_name}")
        try:
            _write_unit_file(unit_dir, service_name, content)
        except OSError as exc:
            warnings.append(f"cannot write unit file: {exc}")
        else:
            _log("unit file written")
            try:
                _log("reloading systemd: systemctl daemon-reload")
                run_command(
                    list(values.SYSTEMCTL_DAEMON_RELOAD_COMMAND),
                    timeout=timeout,
                )
                _log("systemd reloaded")
                _log(f"enabling service: systemctl enable {service_name}")
                run_command(
                    substituted_command(
                        values.SYSTEMCTL_ENABLE_COMMAND,
                        {"service_unit_name": service_name},
                    ),
                    timeout=timeout,
                )
                _log("service enabled")
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ) as exc:
                warnings.append(f"systemd setup failed: {exc}")

    # The closing line names every parameter with the value it was given, in
    # write order: the names come from the table and the task knows none of
    # them itself.
    configured_values = ", ".join(
        f"{name} {value}" for name, value in target.items()
    )
    message = f"zswap configured: {configured_values}"
    if warnings:
        message = f"{message}; {'; '.join(warnings)}"
    return TaskResult(
        success=True,
        changed=changed,
        message=message,
        warnings=tuple(warnings),
    )
