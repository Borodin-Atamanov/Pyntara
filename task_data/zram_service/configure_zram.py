#!/usr/bin/python3
"""Ensure the ZRAM swap devices of this machine and activate them.

This program is deployed by the zram_service task and is started by the boot
service of that task, so it runs with the system interpreter, imports the
standard library alone and never reaches into the pyntara package: the
deployment venv of this project belongs to a later task, and the boot path must
not depend on it. Every policy value arrives as a command line argument, so
those values live in the values module of the task and nowhere else; the tool
names, the kernel paths and the shapes of the commands this program runs are
its own implementation.

The program reads the installed memory and the number of CPU cores at every
run, so a machine that is restarted with another memory size or another core
count is configured for what it really has at that boot. The device count
equals the core count, and the total capacity is a fraction of installed RAM
split evenly across the devices and rounded down to the byte boundary the zram
driver requires, so every device carries the same size.

The program is idempotent. It reads the current devices, deactivates and resets
the ones that stay, removes the ones beyond the computed count, creates the
missing ones through hot_add, then configures, formats and activates every
device. Where the target state is already reached and the program is not forced
it changes nothing and says so.

The kernel interface of hot_add changed: kernel 7.0 creates one device on every
read of the attribute and returns the new device id, older kernels create one
device per write. The program tells the two apart by the attribute mode at run
time, which is why the unit no longer has to carry the interface that was
detected when the task ran.

A step that fails but does not stop the run is reported as a warning, so one
device that cannot be configured does not prevent the others. A step the whole
run depends on, such as reading the installed memory, ends the program with an
error.

The last line this program prints is one JSON object for the caller:

    {
        "changed": bool,
        "skipped_reason": str | null,
        "error": str | null,
        "warnings": [str, ...],
    }

The caller reads the result from that line and never parses the sentences. The
exit code is 0 when the program did its work or decided that it must not, and 1
when a step failed, which is repeated in the error key of the same line.
"""

from __future__ import annotations

import argparse
import errno
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Names of the tools this program runs. They are its implementation and live
# here rather than in the values module of the task, because nothing else runs
# them. The absolute path of each one is discovered at run time instead of
# being written down, so a machine that keeps its tools elsewhere is followed
# and a missing tool is answered with the name of that tool.
MODPROBE_TOOL: str = "modprobe"
SWAPON_TOOL: str = "swapon"
SWAPOFF_TOOL: str = "swapoff"
MKSWAP_TOOL: str = "mkswap"

# The kernel interface of the zram devices: the directory that holds one entry
# per device, and the control files that add and remove devices. The whole
# interface is the implementation of this program, so its shape lives here.
SYS_BLOCK_PATH: Path = Path("/sys/block")
ZRAM_CONTROL_DIR: Path = Path("/sys/class/zram-control")
HOT_ADD_FILE_NAME: str = "hot_add"
HOT_REMOVE_FILE_NAME: str = "hot_remove"

# The two control files of that directory, named so that a caller can point
# them at another location, which is how the tests stand in for the kernel that
# creates one device on every read of hot_add.
HOT_ADD_PATH: Path = ZRAM_CONTROL_DIR / HOT_ADD_FILE_NAME
HOT_REMOVE_PATH: Path = ZRAM_CONTROL_DIR / HOT_REMOVE_FILE_NAME

# Names of the attributes of one device, and of the swap listing arguments.
DISKSIZE_ATTRIBUTE_NAME: str = "disksize"
COMPRESSION_ATTRIBUTE_NAME: str = "comp_algorithm"
RESET_ATTRIBUTE_NAME: str = "reset"
SWAP_SHOW_ARGUMENTS: tuple[str, ...] = ("--show", "--noheadings")

# Mode bit that marks the hot_add attribute as readable: a readable attribute
# is the kernel 7.0 read-to-add interface, a write-only one is the older
# write-to-add interface. The bit is a kernel fact and lives here.
HOT_ADD_READABLE_MODE_BIT: int = 0o400

# Byte factor of the memory the kernel reports in kibibytes, and the byte
# factor the total is reported in.
BYTES_PER_KIB: int = 1024
BYTES_PER_MIB: int = 1024 * 1024


class ZramError(Exception):
    """A step could not run, with the sentence the caller must show."""


@dataclass(frozen=True)
class Config:
    """Everything the program needs, all of it read from the command line.

    The policy values, the kernel files the memory and the core count are read
    from and the names of the lines inside them are values of the task and
    arrive here as arguments; the program itself carries no number a caller may
    want to change.
    """

    meminfo_path: Path
    meminfo_total_key: str
    cpuinfo_path: Path
    cpuinfo_processor_key: str
    compressor: str
    swap_priority: int
    memory_fraction_percent: int
    percent_scale: int
    fallback_cpu_count: int
    alignment_bytes: int
    bytes_per_kib: int
    module_name: str
    reset_busy_attempts: int
    reset_busy_retry_delay_seconds: float
    command_timeout_seconds: float
    force: bool


@dataclass(frozen=True)
class Outcome:
    """What the program did, as the caller reads it from the result line.

    The warnings are the steps that failed without stopping the run; the caller
    reports them as warnings of the task, because the machine stays usable but
    the target state is not fully reached.
    """

    changed: bool
    skipped_reason: str | None
    warnings: tuple[str, ...]


def _tool_path(tool_name: str) -> str:
    """Absolute path of one tool this program runs.

    The path is discovered at run time instead of being written down, so a
    machine that keeps its tools elsewhere is followed, and a machine without
    the tool is answered with the name of that tool rather than a traceback.
    """

    path = shutil.which(tool_name)
    if path is None:
        raise ZramError(f"{tool_name} is not installed on this machine")
    return path


def _run(
    command: list[str], timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    """Run one command and keep its output for the report."""

    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise ZramError(f"{command[0]} is not installed on this machine") from exc


def _failure_sentence(result: subprocess.CompletedProcess[str]) -> str:
    """The sentence a failed command printed, or its exit code."""

    for stream in (result.stderr, result.stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return f"exit code {result.returncode}"


def _read_ram_kib(config: Config) -> int:
    """Installed RAM in kibibytes from the kernel file of the caller.

    The name of the line is a value of the task, because the swapfile task reads
    the same line; the program carries no name of its own.
    """

    try:
        lines = config.meminfo_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ZramError(f"cannot read {config.meminfo_path}: {exc}") from exc
    for line in lines:
        if line.startswith(config.meminfo_total_key):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1])
    raise ZramError(f"{config.meminfo_path} carries no {config.meminfo_total_key} line")


def _read_cpu_count(config: Config) -> tuple[int, bool]:
    """CPU core count and whether the fallback was used.

    The count comes from the processor lines of the kernel file of the caller.
    When the file cannot be read or reports no processors, the configured
    fallback is used and the flag is True.
    """

    try:
        text = config.cpuinfo_path.read_text(encoding="utf-8")
    except OSError:
        return config.fallback_cpu_count, True
    count = sum(
        1 for line in text.splitlines() if line.startswith(config.cpuinfo_processor_key)
    )
    if count == 0:
        return config.fallback_cpu_count, True
    return count, False


def _calculate_devices(ram_kib: int, cpu_count: int, config: Config) -> tuple[int, int]:
    """Target (device_count, per_device_bytes).

    The total capacity is the configured fraction of installed RAM; it is split
    evenly across the devices and rounded down to the configured byte boundary
    that the zram driver requires for disksize.
    """

    total_bytes = (
        ram_kib
        * config.bytes_per_kib
        * config.memory_fraction_percent
        // config.percent_scale
    )
    per_device_bytes = (
        total_bytes // cpu_count // config.alignment_bytes * config.alignment_bytes
    )
    return cpu_count, per_device_bytes


def _device_name(module_name: str, index: int) -> str:
    """Name of one device as the kernel exposes it in /sys/block.

    The name is the configured module name and the index, because the kernel
    names every device of a module that way.
    """

    return f"{module_name}{index}"


def _device_path(module_name: str, index: int) -> str:
    """Path of the swap device the kernel creates for one device."""

    return f"/dev/{_device_name(module_name, index)}"


def _existing_device_indices(module_name: str) -> list[int]:
    """Sorted device indices currently present in /sys/block.

    Iterating the actual indices instead of a numeric range keeps the teardown
    correct when a device is missing in the middle, which happens when a device
    was removed by hand. An entry whose name is the configured module name
    followed by a number is a device of it.
    """

    if not SYS_BLOCK_PATH.is_dir():
        return []
    indices: list[int] = []
    for path in SYS_BLOCK_PATH.iterdir():
        name = path.name
        if not name.startswith(module_name):
            continue
        index_text = name[len(module_name) :]
        if index_text.isdigit():
            indices.append(int(index_text))
    return sorted(indices)


def _read_disksize(index: int, module_name: str) -> int | None:
    """Configured disksize in bytes for one device, or None when unreadable."""

    try:
        text = (
            SYS_BLOCK_PATH.joinpath(
                _device_name(module_name, index), DISKSIZE_ATTRIBUTE_NAME
            )
            .read_text(encoding="utf-8")
            .strip()
        )
        return int(text)
    except OSError, ValueError:
        return None


def _read_active_algorithm(index: int, module_name: str) -> str | None:
    """Currently active compression algorithm for one device, or None.

    comp_algorithm lists every supported algorithm; the active one is marked
    with square brackets, for example lzo lzo-rle [zstd] zstd.
    """

    try:
        text = SYS_BLOCK_PATH.joinpath(
            _device_name(module_name, index), COMPRESSION_ATTRIBUTE_NAME
        ).read_text(encoding="utf-8")
    except OSError:
        return None
    for token in text.split():
        if token.startswith("[") and token.endswith("]"):
            return token[1:-1]
    return None


def _write_sysfs(path: Path, value: str) -> None:
    """Write one value into a sysfs attribute file.

    Raises OSError when the attribute does not exist or the kernel rejects the
    value.
    """

    path.write_text(value, encoding="utf-8")


def _write_sysfs_with_retry(path: Path, value: str, config: Config) -> None:
    """Write a sysfs attribute, retrying when the device is transiently busy.

    The kernel returns EBUSY for reset and hot_remove while the device is
    momentarily open, for example by a udev blkid probe triggered by a preceding
    event; the condition clears within milliseconds. The write is retried with a
    short pause until the configured attempts run out, then the last error is
    re-raised so the caller reports it. Other errors are raised immediately:
    they are not transient.
    """

    for attempt in range(config.reset_busy_attempts):
        try:
            _write_sysfs(path, value)
            return
        except OSError as exc:
            if exc.errno != errno.EBUSY:
                raise
            if attempt + 1 >= config.reset_busy_attempts:
                raise
            time.sleep(config.reset_busy_retry_delay_seconds)


def _hot_add_read_interface() -> bool:
    """True when hot_add is the read-to-add interface (kernel 7.0+).

    Kernel 7.0 creates a zram device on every read of hot_add and returns the
    new device id; older kernels create one device per write. The interface is
    told apart by the attribute mode: readable files are read-to-add, write-only
    files are write-to-add. The mode query itself does not create a device.
    """

    mode = HOT_ADD_PATH.stat().st_mode
    return bool(mode & HOT_ADD_READABLE_MODE_BIT)


def _add_devices(count: int, read_interface: bool, module_name: str) -> str | None:
    """Create devices via hot_add; return an error message or None.

    On the read-to-add interface every read creates one device and returns its
    id, which is logged; on the write interface every write creates one device.
    A failed operation returns a message and stops the addition.
    """

    hot_add_path = HOT_ADD_PATH
    for _ in range(count):
        if read_interface:
            try:
                text = hot_add_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                return f"cannot add {module_name} devices: {exc}"
            try:
                device_id = int(text)
            except ValueError:
                return f"cannot add devices: {HOT_ADD_FILE_NAME} returned {text!r}"
            print(f"device added: {_device_name(module_name, device_id)}")
        else:
            try:
                _write_sysfs(hot_add_path, "1")
            except OSError as exc:
                return f"cannot add {module_name} devices: {exc}"
            print(f"device added via {HOT_ADD_FILE_NAME} write")
    return None


def _active_swap_paths(timeout_seconds: float) -> tuple[str, ...]:
    """Paths of every active swap device, including the disk swapfile.

    The listing command reports all activated swaps; the tuple includes both the
    zram devices and a file-backed swap such as /swap/swapfile, so the caller
    checks the zram paths it cares about individually.
    """

    command = [_tool_path(SWAPON_TOOL), *SWAP_SHOW_ARGUMENTS]
    result = _run(command, timeout_seconds)
    if result.returncode != 0:
        raise ZramError(f"cannot list the active swap: {_failure_sentence(result)}")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields:
            paths.append(fields[0])
    return tuple(paths)


def _target_reached(
    device_count: int,
    per_device_bytes: int,
    active_paths: tuple[str, ...],
    config: Config,
) -> bool:
    """True when every device exists at the target size with the target
    algorithm and is active, and no extra device exists.
    """

    if len(_existing_device_indices(config.module_name)) != device_count:
        return False
    for index in range(device_count):
        if _read_disksize(index, config.module_name) != per_device_bytes:
            return False
        if _read_active_algorithm(index, config.module_name) != config.compressor:
            return False
        if _device_path(config.module_name, index) not in active_paths:
            return False
    return True


def _remove_extra_devices(
    existing_indices: list[int],
    device_count: int,
    active_paths: tuple[str, ...],
    config: Config,
    warnings: list[str],
) -> None:
    """Deactivate the devices that stay, reset them, and remove the extras.

    Every existing device is deactivated before its size or algorithm is
    rewritten, and a device beyond the target count is removed entirely. A step
    that fails is reported as a warning, so the remaining devices are still
    handled.
    """

    for index in existing_indices:
        name = _device_name(config.module_name, index)
        device_path = _device_path(config.module_name, index)
        if device_path in active_paths:
            print(f"deactivating swap: {SWAPOFF_TOOL} {device_path}")
            try:
                result = _run(
                    [_tool_path(SWAPOFF_TOOL), device_path],
                    config.command_timeout_seconds,
                )
            except ZramError as exc:
                warnings.append(f"cannot deactivate {device_path}: {exc}")
            else:
                if result.returncode == 0:
                    print("swap deactivated")
                else:
                    warnings.append(
                        f"cannot deactivate {device_path}: {_failure_sentence(result)}"
                    )
        if index < device_count:
            print(f"resetting device {name}")
            try:
                _write_sysfs_with_retry(
                    SYS_BLOCK_PATH / name / RESET_ATTRIBUTE_NAME, "1", config
                )
            except OSError as exc:
                warnings.append(f"cannot reset {name}: {exc}")
            else:
                print("device reset")
        else:
            print(f"removing extra device {name}")
            try:
                _write_sysfs_with_retry(HOT_REMOVE_PATH, str(index), config)
            except OSError as exc:
                warnings.append(f"cannot remove {name}: {exc}")
            else:
                print("device removed")


def _load_module(config: Config, warnings: list[str]) -> None:
    """Load the kernel module that provides the compressed devices."""

    print(f"loading module: {MODPROBE_TOOL} {config.module_name}")
    try:
        result = _run(
            [_tool_path(MODPROBE_TOOL), config.module_name],
            config.command_timeout_seconds,
        )
    except ZramError as exc:
        warnings.append(f"cannot load {config.module_name} module: {exc}")
        return
    if result.returncode == 0:
        print("module loaded")
    else:
        warnings.append(
            f"cannot load {config.module_name} module: {_failure_sentence(result)}"
        )


def _configure_devices(
    device_count: int, per_device_bytes: int, config: Config, warnings: list[str]
) -> bool:
    """Configure every device: algorithm, size, signature, activation.

    The devices are configured in order, and a device that cannot be configured
    is reported and skipped so the others still come up. True means that at
    least one device was changed by this run.
    """

    changed = False
    for index in range(device_count):
        name = _device_name(config.module_name, index)
        device_path = _device_path(config.module_name, index)
        print(f"configuring {name}: algorithm {config.compressor}")
        try:
            _write_sysfs(
                SYS_BLOCK_PATH / name / COMPRESSION_ATTRIBUTE_NAME, config.compressor
            )
            _write_sysfs(
                SYS_BLOCK_PATH / name / DISKSIZE_ATTRIBUTE_NAME, str(per_device_bytes)
            )
        except OSError as exc:
            warnings.append(f"cannot configure {name}: {exc}")
            continue
        print(f"{name} configured: {per_device_bytes} bytes")
        print(f"formatting {name}: {MKSWAP_TOOL} {device_path}")
        try:
            result = _run(
                [_tool_path(MKSWAP_TOOL), device_path], config.command_timeout_seconds
            )
            if result.returncode != 0:
                warnings.append(f"{name} setup failed: {_failure_sentence(result)}")
                continue
            print(f"activating {name}: {SWAPON_TOOL} --priority {config.swap_priority}")
            result = _run(
                [
                    _tool_path(SWAPON_TOOL),
                    "--priority",
                    str(config.swap_priority),
                    device_path,
                ],
                config.command_timeout_seconds,
            )
        except ZramError as exc:
            warnings.append(f"{name} setup failed: {exc}")
            continue
        if result.returncode != 0:
            warnings.append(f"{name} setup failed: {_failure_sentence(result)}")
            continue
        print(f"{name} active")
        changed = True
    return changed


def _verify(device_count: int, per_device_bytes: int, config: Config) -> list[str]:
    """Read the configured state back and report what does not hold.

    The verification reads the system files instead of trusting the writes,
    because a write the kernel ignored would otherwise leave a machine that
    reports success without the ZRAM swap it promised.
    """

    print(f"verifying {config.module_name} configuration")
    problems: list[str] = []
    try:
        active_paths = _active_swap_paths(config.command_timeout_seconds)
    except ZramError as exc:
        return [f"cannot verify the active swap: {exc}"]
    for index in range(device_count):
        name = _device_name(config.module_name, index)
        if _read_disksize(index, config.module_name) != per_device_bytes:
            problems.append(f"{name} disksize mismatch")
        if _read_active_algorithm(index, config.module_name) != config.compressor:
            problems.append(f"{name} algorithm mismatch")
        if _device_path(config.module_name, index) not in active_paths:
            problems.append(f"{name} not active")
    if len(_existing_device_indices(config.module_name)) != device_count:
        problems.append(f"extra {config.module_name} devices present")
    if not problems:
        print("verification passed")
    return problems


def configure_zram(config: Config) -> Outcome:
    """Bring the ZRAM devices of this machine to the computed state.

    The target is computed from the memory and the core count read at this very
    run, so a machine restarted with another memory size or another core count
    is configured for what it has. Where the target state is already reached and
    the program is not forced, nothing changes.
    """

    warnings: list[str] = []
    ram_kib = _read_ram_kib(config)
    cpu_count, cpu_fallback = _read_cpu_count(config)
    device_count, per_device_bytes = _calculate_devices(ram_kib, cpu_count, config)
    total_mb = per_device_bytes * device_count // BYTES_PER_MIB
    print(f"reading RAM from {config.meminfo_path}: {ram_kib // BYTES_PER_KIB} MiB")
    if cpu_fallback:
        print(
            f"reading CPU count from {config.cpuinfo_path}: undeterminable, "
            f"using fallback {config.fallback_cpu_count}"
        )
    else:
        print(f"reading CPU count from {config.cpuinfo_path}: {cpu_count} cores")
    print(
        f"calculated target: {device_count} devices, {per_device_bytes} bytes "
        f"each, total {total_mb} MiB"
    )
    if per_device_bytes <= 0:
        raise ZramError(
            "the computed device size is 0 bytes, so no device is configured; "
            "check the memory fraction and the alignment of the section"
        )

    active_paths = _active_swap_paths(config.command_timeout_seconds)
    existing_indices = _existing_device_indices(config.module_name)
    print(f"checking existing {config.module_name} devices: {len(existing_indices)}")
    print(f"checking active swap devices: {len(active_paths)}")

    if not config.force and _target_reached(
        device_count, per_device_bytes, active_paths, config
    ):
        print("target state already reached, nothing to do")
        return Outcome(changed=False, skipped_reason=None, warnings=tuple(warnings))

    _remove_extra_devices(
        existing_indices, device_count, active_paths, config, warnings
    )
    _load_module(config, warnings)

    read_interface = False
    try:
        read_interface = _hot_add_read_interface()
    except OSError as exc:
        # The write spelling of the interface is the fallback, and the creation
        # step reports its own failure if the module is absent.
        warnings.append(f"cannot query {HOT_ADD_FILE_NAME}: {exc}")
    print(f"{HOT_ADD_FILE_NAME} interface: {'read' if read_interface else 'write'}")

    missing = device_count - len(_existing_device_indices(config.module_name))
    if missing > 0:
        print(f"creating missing devices: {HOT_ADD_FILE_NAME} {missing} times")
        add_error = _add_devices(missing, read_interface, config.module_name)
        if add_error is not None:
            warnings.append(add_error)
        else:
            print("devices created")

    changed = _configure_devices(device_count, per_device_bytes, config, warnings)

    problems = _verify(device_count, per_device_bytes, config)
    if problems:
        warnings.append("; ".join(problems))

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    return Outcome(changed=changed, skipped_reason=None, warnings=tuple(warnings))


def _build_parser() -> argparse.ArgumentParser:
    """The command line of the program: every value the caller owns."""

    parser = argparse.ArgumentParser(
        prog="configure_zram",
        description="Ensure the ZRAM swap devices of this machine and activate them.",
    )
    parser.add_argument(
        "--meminfo", required=True, help="kernel file the memory is read from"
    )
    parser.add_argument(
        "--meminfo-total-key",
        required=True,
        help="name of the line in that file that carries the memory",
    )
    parser.add_argument(
        "--cpuinfo", required=True, help="kernel file the core count is read from"
    )
    parser.add_argument(
        "--cpuinfo-processor-key",
        required=True,
        help="name of the per-core line in that file",
    )
    parser.add_argument(
        "--compressor", required=True, help="compression algorithm of every device"
    )
    parser.add_argument(
        "--swap-priority", required=True, type=int, help="swap priority of the devices"
    )
    parser.add_argument(
        "--memory-fraction-percent",
        required=True,
        type=int,
        help="share of installed RAM the devices carry in total",
    )
    parser.add_argument(
        "--percent-scale",
        required=True,
        type=int,
        help="denominator the percentage is computed against",
    )
    parser.add_argument(
        "--fallback-cpu-count",
        required=True,
        type=int,
        help="core count used when the real count cannot be read",
    )
    parser.add_argument(
        "--alignment-bytes",
        required=True,
        type=int,
        help="byte boundary the device size is rounded down to",
    )
    parser.add_argument(
        "--bytes-per-kib",
        required=True,
        type=int,
        help="byte factor of the memory the kernel reports",
    )
    parser.add_argument(
        "--module-name", required=True, help="kernel module that provides the devices"
    )
    parser.add_argument(
        "--reset-busy-attempts",
        required=True,
        type=int,
        help="attempts of a reset or removal the kernel rejects while busy",
    )
    parser.add_argument(
        "--reset-busy-retry-delay-seconds",
        required=True,
        type=float,
        help="pause between two attempts of that retry",
    )
    parser.add_argument(
        "--command-timeout-seconds",
        required=True,
        type=float,
        help="bound of every command this program runs",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="configure the devices again even where the target state is reached",
    )
    return parser


def _config_from_arguments(argv: list[str]) -> Config:
    """Read the command line into the typed configuration of one run."""

    arguments = _build_parser().parse_args(argv)
    return Config(
        meminfo_path=Path(arguments.meminfo),
        meminfo_total_key=arguments.meminfo_total_key,
        cpuinfo_path=Path(arguments.cpuinfo),
        cpuinfo_processor_key=arguments.cpuinfo_processor_key,
        compressor=arguments.compressor,
        swap_priority=arguments.swap_priority,
        memory_fraction_percent=arguments.memory_fraction_percent,
        percent_scale=arguments.percent_scale,
        fallback_cpu_count=arguments.fallback_cpu_count,
        alignment_bytes=arguments.alignment_bytes,
        bytes_per_kib=arguments.bytes_per_kib,
        module_name=arguments.module_name,
        reset_busy_attempts=arguments.reset_busy_attempts,
        reset_busy_retry_delay_seconds=arguments.reset_busy_retry_delay_seconds,
        command_timeout_seconds=arguments.command_timeout_seconds,
        force=arguments.force,
    )


def main(argv: list[str]) -> int:
    """Run the program and print its result line for the caller."""

    config = _config_from_arguments(argv)
    try:
        outcome = configure_zram(config)
    except (ZramError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "changed": False,
                    "skipped_reason": None,
                    "error": str(exc),
                    "warnings": [],
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "changed": outcome.changed,
                "skipped_reason": outcome.skipped_reason,
                "error": None,
                "warnings": list(outcome.warnings),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
