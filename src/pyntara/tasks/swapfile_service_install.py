"""Task swapfile_service_install: calculate and configure a swapfile.

The swap size is min(RAM * ram_multiplier + ram_extra_mb,
free_disk * disk_fraction), where the parameters come from config.toml
through ctx.config.swapfile_service_install and the RAM and free disk
space are measured on the target machine. The task creates the swapfile
with fallocate, formats it with mkswap, activates it with swapon and
installs a systemd oneshot service that re-activates the swap at every
boot. The unit file is rendered from the template at
task_data/swapfile_service_install/swapfile.service with the swapfile
path substituted (string.Template); the service never reads config.toml
itself. The task is idempotent: it skips when the swapfile already has
the computed size, is active and the service is enabled; force mode
reruns it and recreates the swapfile.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from string import Template

from pyntara.config import SwapfileServiceInstallConfig
from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import run_command

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = REPO_ROOT / "task_data" / "swapfile_service_install" / "swapfile.service"
SWAPFILE_SERVICE_NAME = "swapfile.service"
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
MEMINFO_PATH = Path("/proc/meminfo")


def _read_ram_kib() -> int:
    """Total installed RAM in kibibytes from /proc/meminfo.

    Raises OSError when the file cannot be read or MemTotal is missing.
    """

    for line in MEMINFO_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1])
    raise OSError(f"{MEMINFO_PATH} has no MemTotal line")


def _calculate_swap_size_mb(
    ram_kib: int, free_disk_kib: int, cfg: SwapfileServiceInstallConfig
) -> int:
    """Swap size in mebibytes: min(RAM*mult+extra, free*disk_fraction).

    The RAM-based size comes from installed RAM scaled by ram_multiplier
    plus the flat ram_extra_mb; the disk-based size is the free space of
    the swapfile partition scaled by disk_fraction. The smaller of the two
    wins, so the swap never risks filling the disk.
    """

    ram_mb = ram_kib // 1024
    ram_based = int(ram_mb * cfg.ram_multiplier) + cfg.ram_extra_mb
    disk_based = int(free_disk_kib // 1024 * cfg.disk_fraction)
    return min(ram_based, disk_based)


def _current_swap_size_mb(path: Path) -> int | None:
    """Size of the swapfile in mebibytes, or None when the file is absent."""

    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    return size // (1024 * 1024)


def _swap_active(path: Path, timeout: float) -> bool:
    """True when the swapfile is currently activated (swapon --show)."""

    result = run_command(
        ["swapon", "--show", "--noheadings"],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and str(path) in result.stdout


def _service_enabled(name: str, timeout: float) -> bool:
    """True when the systemd service is enabled for boot."""

    result = run_command(
        ["systemctl", "is-enabled", name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and result.stdout.strip() == "enabled"


def _render_unit(template_path: Path, swapfile_path: Path) -> str:
    """Render the service unit template with the swapfile path substituted."""

    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(swapfile_path=str(swapfile_path))


def _write_unit_file(unit_dir: Path, service_name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / service_name).write_text(content, encoding="utf-8")


def task(ctx: Context) -> TaskResult:
    """Configure the swapfile and the activation service; skip when done.

    The goal is reached when the swapfile exists at the computed size, is
    active and the service is enabled; the task then returns changed=False.
    Otherwise it deactivates and removes an obsolete swapfile, creates a
    new one at the computed size, activates it, writes the unit file and
    enables the service. Any failure is returned as an error TaskResult:
    the runner continues with the remaining tasks and never stops here.
    """

    cfg = ctx.config.swapfile_service_install
    timeout = ctx.config.engine.command_timeout_seconds
    force = "swapfile_service_install" in ctx.force_tasks

    try:
        ram_kib = _read_ram_kib()
        free_disk_kib = shutil.disk_usage(cfg.swapfile_path.parent).free // 1024
    except OSError as exc:
        return TaskResult(
            success=False, error=f"cannot determine RAM or free disk space: {exc}"
        )

    target_mb = _calculate_swap_size_mb(ram_kib, free_disk_kib, cfg)
    current_mb = _current_swap_size_mb(cfg.swapfile_path)
    active = _swap_active(cfg.swapfile_path, timeout)
    enabled = _service_enabled(SWAPFILE_SERVICE_NAME, timeout)

    if (
        not force
        and current_mb is not None
        and abs(current_mb - target_mb) <= 1
        and active
        and enabled
    ):
        return TaskResult(success=True, changed=False, message="already configured")

    changed = False
    if not force and current_mb is not None and abs(current_mb - target_mb) <= 1:
        # The swapfile exists at the computed size; only activation or the
        # service is missing, so no recreation is needed.
        if not active:
            try:
                run_command(["swapon", str(cfg.swapfile_path)], timeout=timeout)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                return TaskResult(success=False, error=f"swapon failed: {exc}")
            changed = True
    else:
        # Recreate the swapfile at the computed size. An active swap must be
        # deactivated first, or the resize would fail on a busy file.
        if active:
            try:
                run_command(["swapoff", str(cfg.swapfile_path)], timeout=timeout)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                return TaskResult(
                    success=False, error=f"cannot deactivate old swapfile: {exc}"
                )
        try:
            cfg.swapfile_path.unlink(missing_ok=True)
        except OSError as exc:
            return TaskResult(success=False, error=f"cannot remove old swapfile: {exc}")
        try:
            run_command(
                ["fallocate", "-l", f"{target_mb}M", str(cfg.swapfile_path)],
                timeout=timeout,
            )
            run_command(["chmod", "600", str(cfg.swapfile_path)], timeout=timeout)
            run_command(["mkswap", str(cfg.swapfile_path)], timeout=timeout)
            run_command(["swapon", str(cfg.swapfile_path)], timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(success=False, error=f"swapfile setup failed: {exc}")
        changed = True

    try:
        content = _render_unit(TEMPLATE_PATH, cfg.swapfile_path)
    except OSError as exc:
        return TaskResult(
            success=False, changed=changed, error=f"cannot read unit template: {exc}"
        )
    try:
        _write_unit_file(SYSTEMD_UNIT_DIR, SWAPFILE_SERVICE_NAME, content)
    except OSError as exc:
        return TaskResult(
            success=False, changed=changed, error=f"cannot write unit file: {exc}"
        )
    try:
        run_command(["systemctl", "daemon-reload"], timeout=timeout)
        run_command(["systemctl", "enable", SWAPFILE_SERVICE_NAME], timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(success=False, changed=True, error=f"systemd setup failed: {exc}")
    return TaskResult(
        success=True,
        changed=True,
        message=f"swapfile {target_mb}M configured at {cfg.swapfile_path}",
    )
