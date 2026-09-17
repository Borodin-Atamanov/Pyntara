"""Task swapfile_service_install: calculate and configure a swapfile.

The swap size is min(RAM * ram_multiplier + ram_extra_mb,
free_disk * disk_fraction), where the parameters come from the values module
of this task and the RAM and free disk space are measured on the target
machine. The task creates the swapfile with the configured allocation command,
writes its signature with the configured format command, activates it with the
configured swap commands and installs a systemd oneshot service that
re-activates the swap at every boot. The unit file is rendered from the template
the values name under task_data/swapfile_service_install/ with the swapfile path
substituted (string.Template); the service never reads the values itself. The
task is idempotent: it skips when the swapfile already has the computed size, is
active and the service is enabled; force mode reruns it and recreates the
swapfile.
"""

from __future__ import annotations

import shutil
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
from pyntara.values import missing_value_names
from pyntara.values import swapfile_service_install as values

# Path of the kernel information the RAM size is read from.
MEMINFO_PATH = Path("/proc/meminfo")


def _read_ram_kib() -> int:
    """Total installed RAM in kibibytes from /proc/meminfo.

    Raises OSError when the file cannot be read or the configured total
    line is missing.
    """

    for line in MEMINFO_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith(common_values.MEMINFO_TOTAL_KEY):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1])
    raise OSError(f"{MEMINFO_PATH} has no {common_values.MEMINFO_TOTAL_KEY} line")


def _calculate_swap_size_mb(
    ram_kib: int,
    free_disk_kib: int,
    bytes_per_kib: int,
) -> int:
    """Swap size in mebibytes: min(RAM*mult+extra, free*disk_fraction).

    The RAM-based size comes from installed RAM scaled by ram_multiplier
    plus the flat ram_extra_mb; the disk-based size is the free space of
    the swapfile partition scaled by disk_fraction. The smaller of the two
    wins, so the swap never risks filling the disk. The byte factor comes
    from the engine table.
    """

    ram_mb = ram_kib // bytes_per_kib
    ram_based = int(ram_mb * values.RAM_MULTIPLIER) + values.RAM_EXTRA_MB
    disk_based = int(free_disk_kib // bytes_per_kib * values.DISK_FRACTION)
    return min(ram_based, disk_based)


def _current_swap_size_mb(path: Path, bytes_per_mib: int) -> int | None:
    """Size of the swapfile in mebibytes, or None when the file is absent."""

    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    return size // bytes_per_mib


def _swap_active(timeout: float) -> bool:
    """True when the swapfile is currently activated.

    The configured swap listing command reports every active swap device;
    the configured swapfile path found in its output means the file is in
    use.
    """

    result = run_command(
        list(values.SWAP_SHOW_COMMAND),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and str(values.SWAPFILE_PATH) in result.stdout


def _render_unit(template_path: Path, swapfile_path: Path) -> str:
    """Render the service unit template with the swapfile path substituted."""

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(swapfile_path=str(swapfile_path))


def _write_unit_file(unit_dir: Path, service_name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / service_name).write_text(content, encoding="utf-8")


def _result(*, changed: bool, message: str, warnings: list[str]) -> TaskResult:
    """Build the result of the task, carrying the warning of a skipped step."""

    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


def task(ctx: Context) -> TaskResult:
    """Configure the swapfile and the activation service; skip when done.

    The goal is reached when the swapfile exists at the computed size, is
    active and the service is enabled; the task then returns changed=False.
    Otherwise it deactivates and removes an obsolete swapfile, creates a
    new one at the computed size, activates it, writes the unit file and
    enables the service. Every step is reported to stdout: measurements and
    decisions as single lines that include their result, long-running
    commands as a line before and a line after. A step that cannot run is
    reported as a warning of a completed task: the missing mechanism skips
    that step alone and every independent step still runs, so the runner
    continues with the remaining tasks and never stops here.
    """

    absent = missing_value_names(
        values, values.READ_VALUE_NAMES
    ) + missing_value_names(common_values, common_values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read.
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
    timeout = ctx.config.engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    service_name = values.SERVICE_UNIT_NAME
    bytes_per_kib = ctx.config.engine.bytes_per_kib
    bytes_per_mib = ctx.config.engine.bytes_per_mib
    warnings: list[str] = []

    measured = True
    try:
        ram_kib = _read_ram_kib()
        free_disk_kib = (
            shutil.disk_usage(values.SWAPFILE_PATH.parent).free // bytes_per_kib
        )
    except OSError as exc:
        # Without the measurements the size cannot be computed, so the
        # swapfile steps are skipped while the boot service is still
        # installed, because the unit creates the file at boot on its own.
        warnings.append(f"cannot determine RAM or free disk space: {exc}")
        measured = False
        ram_kib = 0
        free_disk_kib = 0

    ram_mb = ram_kib // bytes_per_kib
    free_disk_mb = free_disk_kib // bytes_per_kib
    _log(f"reading RAM from {MEMINFO_PATH}: {ram_mb} MiB")
    _log(
        f"reading free disk space on {values.SWAPFILE_PATH.parent}: "
        f"{free_disk_mb} MiB"
    )

    multiplier = values.RAM_MULTIPLIER
    multiplier_text = (
        str(int(multiplier)) if multiplier.is_integer() else str(multiplier)
    )
    fraction = values.DISK_FRACTION
    fraction_text = str(int(fraction)) if fraction.is_integer() else str(fraction)
    target_mb = _calculate_swap_size_mb(ram_kib, free_disk_kib, bytes_per_kib)
    _log(
        f"calculated target size: min({ram_mb} MiB * {multiplier_text} + "
        f"{values.RAM_EXTRA_MB} MiB, {free_disk_mb} MiB * {fraction_text}) = "
        f"{target_mb} MiB"
    )

    current_mb = _current_swap_size_mb(values.SWAPFILE_PATH, bytes_per_mib)
    if current_mb is None:
        _log(f"checking swapfile {values.SWAPFILE_PATH}: absent")
    else:
        _log(
            f"checking swapfile {values.SWAPFILE_PATH}: exists, size: "
            f"{current_mb} MiB"
        )
    active = _swap_active(timeout)
    _log(f"checking system service activation: {'active' if active else 'inactive'}")
    enabled = service_is_enabled(ctx.config.engine, service_name, timeout)
    _log(
        f"checking autorun service {service_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )

    if (
        measured
        and not force
        and current_mb is not None
        and abs(current_mb - target_mb) <= values.SIZE_TOLERANCE_MB
        and active
        and enabled
    ):
        _log("target state already reached, skipping")
        return _result(changed=False, message="already configured", warnings=warnings)

    changed = False
    if (
        measured
        and not force
        and current_mb is not None
        and abs(current_mb - target_mb) <= values.SIZE_TOLERANCE_MB
    ):
        # The swapfile exists at the computed size; only activation or the
        # service is missing, so no recreation is needed.
        _log(f"swapfile already at target size: {current_mb} MiB")
        if not active:
            _log(f"activating swap: swapon {values.SWAPFILE_PATH}")
            try:
                run_command(
                    substituted_command(
                        values.SWAP_ON_COMMAND,
                        {"swapfile_path": str(values.SWAPFILE_PATH)},
                    ),
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"swapon failed: {exc}")
            else:
                _log("swap active")
                changed = True
    elif measured or not current_mb:
        # Recreate the swapfile at the computed size. An active swap must be
        # deactivated first, or the resize would fail on a busy file.
        if force:
            _log(f"force mode, recreating swapfile at target size {target_mb} MiB")
        else:
            current_text = f"{current_mb} MiB" if current_mb is not None else "missing"
            _log(
                f"swapfile size {current_text} differs from target "
                f"{target_mb} MiB, recreating"
            )
        deactivated = True
        if active:
            _log(f"deactivating swap: swapoff {values.SWAPFILE_PATH}")
            try:
                run_command(
                    substituted_command(
                        values.SWAP_OFF_COMMAND,
                        {"swapfile_path": str(values.SWAPFILE_PATH)},
                    ),
                    timeout=timeout,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                # A swap that could not be deactivated is still in use, so
                # the file is left alone instead of being removed under it.
                warnings.append(f"cannot deactivate old swapfile: {exc}")
                deactivated = False
            else:
                _log("swap deactivated")
        removed = True
        if deactivated:
            _log(f"removing old swapfile {values.SWAPFILE_PATH}")
            try:
                values.SWAPFILE_PATH.unlink(missing_ok=True)
            except OSError as exc:
                warnings.append(f"cannot remove old swapfile: {exc}")
                removed = False
            else:
                _log("old swapfile removed")
        if not removed:
            _log("skipping swapfile creation: the old file could not be removed")
        else:
            try:
                _log(
                    f"creating swapfile: fallocate -l {target_mb}M "
                    f"{values.SWAPFILE_PATH}"
                )
                run_command(
                    substituted_command(
                        values.CREATE_COMMAND,
                        {
                            "size_mb": str(target_mb),
                            "swapfile_path": str(values.SWAPFILE_PATH),
                        },
                    ),
                    timeout=timeout,
                )
                _log(f"swapfile created: {target_mb} MiB")
                _log(
                    f"setting permissions: chmod {values.SWAPFILE_MODE:o} "
                    f"{values.SWAPFILE_PATH}"
                )
                run_command(
                    substituted_command(
                        values.CHMOD_COMMAND,
                        {
                            "file_mode": f"{values.SWAPFILE_MODE:o}",
                            "swapfile_path": str(values.SWAPFILE_PATH),
                        },
                    ),
                    timeout=timeout,
                )
                _log("permissions set")
                _log(f"formatting swapfile: mkswap {values.SWAPFILE_PATH}")
                run_command(
                    substituted_command(
                        values.FORMAT_COMMAND,
                        {"swapfile_path": str(values.SWAPFILE_PATH)},
                    ),
                    timeout=timeout,
                )
                _log("swapfile formatted")
                _log(f"activating swap: swapon {values.SWAPFILE_PATH}")
                run_command(
                    substituted_command(
                        values.SWAP_ON_COMMAND,
                        {"swapfile_path": str(values.SWAPFILE_PATH)},
                    ),
                    timeout=timeout,
                )
                _log("swap active")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"swapfile setup failed: {exc}")
            else:
                changed = True

    template_path = (
        task_data_dir(ctx.repo_root, ctx.task_name)
        / values.UNIT_TEMPLATE_FILE_NAME
    )
    _log(f"rendering unit template from {template_path}")
    content: str | None = None
    try:
        content = _render_unit(template_path, values.SWAPFILE_PATH)
    except OSError as exc:
        warnings.append(f"cannot read unit template: {exc}")
    if content is not None:
        unit_dir = ctx.config.engine.systemd_unit_dir
        _log(f"writing unit file {unit_dir / service_name}")
        unit_written = False
        try:
            _write_unit_file(unit_dir, service_name, content)
        except OSError as exc:
            warnings.append(f"cannot write unit file: {exc}")
        else:
            _log("unit file written")
            unit_written = True
            changed = True
        if unit_written:
            try:
                _log("reloading systemd: systemctl daemon-reload")
                run_command(
                    list(values.SYSTEMCTL_DAEMON_RELOAD_COMMAND), timeout=timeout
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
                changed = True
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ) as exc:
                warnings.append(f"systemd setup failed: {exc}")

    if measured:
        message = f"swapfile {target_mb}M configured at {values.SWAPFILE_PATH}"
    else:
        message = (
            f"swapfile service {service_name} configured for "
            f"{values.SWAPFILE_PATH}, size not measured"
        )
    return _result(changed=changed, message=message, warnings=warnings)
