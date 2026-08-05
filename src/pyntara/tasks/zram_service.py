"""Task zram_service: configure aggressive ZRAM swap by CPU and RAM.

The device count equals the number of CPU cores (8 when the count cannot
be determined), the total capacity is 96 percent of installed RAM split
evenly across the devices and rounded down to the 4096-byte zram page
size. Every device uses the zstd compression algorithm and is activated
with swap priority 1111, so ZRAM swap is preferred over the disk
swapfile (docs/spec/users-and-host.md). The task configures the devices
immediately and installs a systemd oneshot service that repeats the same
setup at every boot. The unit file is rendered from the template at
task_data/zram_service/zram.service with the ExecStart block substituted
(string.Template); the service never reads config.toml itself. The task
is idempotent: it skips when every device already exists at the computed
size with zstd active, no extra devices are present and the service is
enabled; force mode tears the devices down and configures them again.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path
from string import Template

from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.utils import run_command

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = REPO_ROOT / "task_data" / "zram_service" / "zram.service"
ZRAM_SERVICE_NAME = "zram.service"
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
MEMINFO_PATH = Path("/proc/meminfo")
CPUINFO_PATH = Path("/proc/cpuinfo")
SYS_BLOCK_PATH = Path("/sys/block")
ZRAM_CONTROL_PATH = Path("/sys/class/zram-control/hot_add")

# Fixed behavior parameters per docs/spec/users-and-host.md: the device
# count follows the CPU core count, the total capacity is 96 percent of
# RAM, compression is aggressive zstd, and swap priority 1111 keeps ZRAM
# ahead of the disk swapfile.
COMPRESSION_ALGORITHM = "zstd"
SWAP_PRIORITY = 1111
MEMORY_FRACTION_NUMERATOR = 96
MEMORY_FRACTION_DENOMINATOR = 100
FALLBACK_CPU_COUNT = 8
ALIGNMENT_BYTES = 4096

# The task name from the catalog; the module file name matches it
# (task-model contract), so the prefix is always correct.
TASK_NAME = __name__.rsplit(".", 1)[-1]

# Monotonic time of the previous progress line; presentation state only, not
# business data (architecture contract forbids business data here, not this).
_last_log_time = 0.0


def _log(message: str) -> None:
    """Print one progress line for this task, flushed to stdout.

    A timestamp in the project datetime format YYYY-MM-DD-HH-MM-SS is
    prepended only when more than one second has passed since the previous
    line, so bursts of lines stay compact. inst.sh tees stdout into the
    install log, so every decision and action of the task is visible in the
    terminal and in the log.
    """

    global _last_log_time
    now = time.monotonic()
    if now - _last_log_time >= 1.0:
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        prefix = f"{timestamp} {TASK_NAME}:"
        _last_log_time = now
    else:
        prefix = f"{TASK_NAME}:"
    print(f"{prefix} {message}", flush=True)


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


def _read_cpu_count() -> tuple[int, bool]:
    """CPU core count and whether the fallback was used.

    The count comes from the processor lines in /proc/cpuinfo. When the
    file cannot be read or reports no processors, the spec fallback of 8
    is used and the flag is True.
    """

    try:
        text = CPUINFO_PATH.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_CPU_COUNT, True
    count = sum(1 for line in text.splitlines() if line.startswith("processor"))
    if count == 0:
        return FALLBACK_CPU_COUNT, True
    return count, False


def _calculate_devices(ram_kib: int, cpu_count: int) -> tuple[int, int]:
    """Target (device_count, per_device_bytes).

    The total capacity is the configured fraction of installed RAM; it is
    split evenly across the devices and rounded down to the 4096-byte
    boundary that the zram driver requires for disksize.
    """

    total_bytes = (
        ram_kib
        * 1024
        * MEMORY_FRACTION_NUMERATOR
        // MEMORY_FRACTION_DENOMINATOR
    )
    per_device_bytes = total_bytes // cpu_count // ALIGNMENT_BYTES * ALIGNMENT_BYTES
    return cpu_count, per_device_bytes


def _existing_device_count() -> int:
    """Number of zram devices currently present in /sys/block."""

    if not SYS_BLOCK_PATH.is_dir():
        return 0
    count = 0
    for path in SYS_BLOCK_PATH.iterdir():
        name = path.name
        if name.startswith("zram") and name[4:].isdigit():
            count += 1
    return count


def _read_disksize(index: int) -> int | None:
    """Configured disksize in bytes for one device, or None when unreadable."""

    try:
        text = (
            SYS_BLOCK_PATH.joinpath(f"zram{index}", "disksize")
            .read_text(encoding="utf-8")
            .strip()
        )
        return int(text)
    except (OSError, ValueError):
        return None


def _read_active_algorithm(index: int) -> str | None:
    """Currently active compression algorithm for one device, or None.

    comp_algorithm lists every supported algorithm; the active one is
    marked with square brackets, for example lzo lzo-rle [zstd] zstd.
    """

    try:
        text = (
            SYS_BLOCK_PATH.joinpath(f"zram{index}", "comp_algorithm")
            .read_text(encoding="utf-8")
        )
    except OSError:
        return None
    for token in text.split():
        if token.startswith("[") and token.endswith("]"):
            return token[1:-1]
    return None


def _write_sysfs(path: Path, value: str) -> None:
    """Write one value into a sysfs attribute file.

    Raises OSError when the attribute does not exist or the kernel
    rejects the value.
    """

    path.write_text(value, encoding="utf-8")


def _zram_active(timeout: float) -> set[str]:
    """Paths of the currently active swap devices (swapon --show)."""

    result = run_command(
        ["swapon", "--show", "--noheadings"],
        check=False,
        capture=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return set()
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        tokens = line.split()
        if tokens:
            paths.add(tokens[0])
    return paths


def _service_enabled(name: str, timeout: float) -> bool:
    """True when the systemd service is enabled for boot."""

    result = run_command(
        ["systemctl", "is-enabled", name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and result.stdout.strip() == "enabled"


def _target_reached(
    device_count: int,
    per_device_bytes: int,
    active_paths: set[str],
    enabled: bool,
) -> bool:
    """True when every device exists at the target size with the target
    algorithm, is active, no extra devices exist and the service is enabled.
    """

    if not enabled:
        return False
    if _existing_device_count() != device_count:
        return False
    for index in range(device_count):
        if _read_disksize(index) != per_device_bytes:
            return False
        if _read_active_algorithm(index) != COMPRESSION_ALGORITHM:
            return False
        if f"/dev/zram{index}" not in active_paths:
            return False
    return True


def _render_unit(
    template_path: Path, device_count: int, per_device_bytes: int
) -> str:
    """Render the service unit template with the ExecStart block substituted.

    The boot service repeats the install-time setup: load the module, add
    the devices past zram0 through hot_add, then configure, format and
    activate every device. The block is fully expanded here, so the
    template carries no shell variables of its own and substitute cannot
    trip on stray dollar signs.
    """

    lines: list[str] = ["ExecStart=/bin/sh -c 'modprobe zram || true'"]
    for index in range(1, device_count):
        lines.append(
            "ExecStart=/bin/sh -c 'echo 1 > /sys/class/zram-control/hot_add'"
        )
    for index in range(device_count):
        lines.append(
            f"ExecStart=/bin/sh -c 'echo {COMPRESSION_ALGORITHM} > "
            f"/sys/block/zram{index}/comp_algorithm'"
        )
        lines.append(
            f"ExecStart=/bin/sh -c 'echo {per_device_bytes} > "
            f"/sys/block/zram{index}/disksize'"
        )
        lines.append(f"ExecStart=/sbin/mkswap /dev/zram{index}")
        lines.append(
            f"ExecStart=/sbin/swapon --priority {SWAP_PRIORITY} /dev/zram{index}"
        )
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(exec_lines="\n".join(lines))


def _write_unit_file(unit_dir: Path, service_name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / service_name).write_text(content, encoding="utf-8")


def task(ctx: Context) -> TaskResult:
    """Configure the ZRAM devices and the boot service; skip when done.

    The goal is reached when every device exists at the computed size with
    the zstd algorithm, is active, no extra devices are present and the
    service is enabled; the task then returns changed=False. Otherwise it
    deactivates and resets the existing devices, removes the extras,
    creates the missing ones, configures every device, writes the unit
    file and enables the service. Every step is reported to stdout:
    measurements and decisions as single lines that include their result,
    long-running commands as a line before and a line after. Any failure
    is returned as an error TaskResult: the runner continues with the
    remaining tasks and never stops here.
    """

    timeout = ctx.config.engine.command_timeout_seconds
    force = "zram_service" in ctx.force_tasks

    try:
        ram_kib = _read_ram_kib()
    except OSError as exc:
        return TaskResult(success=False, error=f"cannot determine RAM size: {exc}")
    cpu_count, cpu_fallback = _read_cpu_count()
    device_count, per_device_bytes = _calculate_devices(ram_kib, cpu_count)
    total_mb = per_device_bytes * device_count // (1024 * 1024)

    _log(f"reading RAM from {MEMINFO_PATH}: {ram_kib // 1024} MiB")
    if cpu_fallback:
        _log(
            f"reading CPU count from {CPUINFO_PATH}: undeterminable, "
            f"using fallback {FALLBACK_CPU_COUNT}"
        )
    else:
        _log(f"reading CPU count from {CPUINFO_PATH}: {cpu_count} cores")
    _log(
        f"calculated target: {device_count} devices, {per_device_bytes} bytes "
        f"each, total {total_mb} MiB"
    )

    active_paths = _zram_active(timeout)
    enabled = _service_enabled(ZRAM_SERVICE_NAME, timeout)
    existing_count = _existing_device_count()
    _log(f"checking existing zram devices: {existing_count}")
    _log(f"checking active zram swaps: {len(active_paths)}")
    _log(
        f"checking autorun service {ZRAM_SERVICE_NAME}: "
        f"{'enabled' if enabled else 'disabled'}"
    )

    if not force and _target_reached(
        device_count, per_device_bytes, active_paths, enabled
    ):
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    # Deactivate, reset and remove the existing devices so sizes and
    # algorithms can be rewritten; devices beyond the target count are
    # removed entirely.
    for index in range(existing_count):
        device_path = f"/dev/zram{index}"
        if device_path in active_paths:
            _log(f"deactivating swap: swapoff {device_path}")
            try:
                run_command(["swapoff", device_path], timeout=timeout)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                return TaskResult(
                    success=False, error=f"cannot deactivate {device_path}: {exc}"
                )
            _log("swap deactivated")
        if index < device_count:
            _log(f"resetting device zram{index}: echo 1 > reset")
            try:
                _write_sysfs(SYS_BLOCK_PATH / f"zram{index}" / "reset", "1")
            except OSError as exc:
                return TaskResult(
                    success=False, error=f"cannot reset zram{index}: {exc}"
                )
            _log("device reset")
        else:
            _log(f"removing extra device zram{index}: echo {index} > hot_remove")
            try:
                _write_sysfs(ZRAM_CONTROL_PATH.with_name("hot_remove"), str(index))
            except OSError as exc:
                return TaskResult(
                    success=False, error=f"cannot remove zram{index}: {exc}"
                )
            _log("device removed")

    # Load the module, then create the devices that are still missing. The
    # module creates zram0 itself; the rest come from hot_add.
    _log("loading module: modprobe zram")
    try:
        run_command(["modprobe", "zram"], timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(success=False, error=f"cannot load zram module: {exc}")
    _log("module loaded")
    missing = device_count - _existing_device_count()
    if missing > 0:
        _log(f"creating missing devices: echo {missing} > hot_add")
        try:
            _write_sysfs(ZRAM_CONTROL_PATH, str(missing))
        except OSError as exc:
            return TaskResult(success=False, error=f"cannot add zram devices: {exc}")
        _log("devices created")

    # Configure every device in order: algorithm, size, swap signature,
    # activation with the configured priority.
    for index in range(device_count):
        device_path = f"/dev/zram{index}"
        _log(f"configuring zram{index}: algorithm {COMPRESSION_ALGORITHM}")
        try:
            _write_sysfs(
                SYS_BLOCK_PATH / f"zram{index}" / "comp_algorithm",
                COMPRESSION_ALGORITHM,
            )
            _write_sysfs(
                SYS_BLOCK_PATH / f"zram{index}" / "disksize",
                str(per_device_bytes),
            )
        except OSError as exc:
            return TaskResult(success=False, error=f"cannot configure zram{index}: {exc}")
        _log(f"zram{index} configured: {per_device_bytes} bytes")
        _log(f"formatting zram{index}: mkswap {device_path}")
        try:
            run_command(["mkswap", device_path], timeout=timeout)
            _log(f"activating zram{index}: swapon --priority {SWAP_PRIORITY}")
            run_command(
                ["swapon", "--priority", str(SWAP_PRIORITY), device_path],
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(success=False, error=f"zram{index} setup failed: {exc}")
        _log(f"zram{index} active")
    changed = True

    # Verify the configured state by reading the system files back.
    _log("verifying zram configuration")
    problems: list[str] = []
    verified_active = _zram_active(timeout)
    for index in range(device_count):
        if _read_disksize(index) != per_device_bytes:
            problems.append(f"zram{index} disksize mismatch")
        if _read_active_algorithm(index) != COMPRESSION_ALGORITHM:
            problems.append(f"zram{index} algorithm mismatch")
        if f"/dev/zram{index}" not in verified_active:
            problems.append(f"zram{index} not active")
    if _existing_device_count() != device_count:
        problems.append("extra zram devices present")
    if problems:
        return TaskResult(success=False, changed=True, error="; ".join(problems))
    _log("verification passed")

    _log(f"rendering unit template from {TEMPLATE_PATH}")
    try:
        content = _render_unit(TEMPLATE_PATH, device_count, per_device_bytes)
    except OSError as exc:
        return TaskResult(
            success=False, changed=changed, error=f"cannot read unit template: {exc}"
        )
    _log(f"writing unit file {SYSTEMD_UNIT_DIR / ZRAM_SERVICE_NAME}")
    try:
        _write_unit_file(SYSTEMD_UNIT_DIR, ZRAM_SERVICE_NAME, content)
    except OSError as exc:
        return TaskResult(
            success=False, changed=changed, error=f"cannot write unit file: {exc}"
        )
    _log("unit file written")
    try:
        _log("reloading systemd: systemctl daemon-reload")
        run_command(["systemctl", "daemon-reload"], timeout=timeout)
        _log("systemd reloaded")
        _log(f"enabling service: systemctl enable {ZRAM_SERVICE_NAME}")
        run_command(["systemctl", "enable", ZRAM_SERVICE_NAME], timeout=timeout)
        _log("service enabled")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TaskResult(
            success=False, changed=True, error=f"systemd setup failed: {exc}"
        )
    return TaskResult(
        success=True,
        changed=True,
        message=(
            f"zram configured: {device_count} devices, "
            f"{per_device_bytes} bytes each, total {total_mb} MiB"
        ),
    )
