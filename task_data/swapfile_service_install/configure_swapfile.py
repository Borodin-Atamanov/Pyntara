#!/usr/bin/python3
"""Ensure the swap file of this machine and activate it.

This program is deployed by the swapfile_service_install task and is started by
the boot service of that task, so it runs with the system interpreter, imports
the standard library alone and never reaches into the pyntara package: the
deployment venv of this project belongs to a later task, and the boot path must
not depend on it. Every value arrives as a command line argument, so the values
live in the values module of the task and nowhere else; the commands this
program runs and the file names it derives are its own implementation and are
written here.

The program is idempotent. It reads the state of the machine, brings the swap
file to the computed size, formats it and activates it, and where the target
state is already reached it changes nothing. The size is
min(installed RAM * ram_multiplier + ram_extra_mb, free disk * disk_fraction),
so a machine with room to spare gets the swap the RAM asks for and a small disk
never gets a file that fills it.

Storage that keeps its data in memory is refused before the real size is
allocated: a probe file of a few kibibytes is created next to the swap file,
formatted and activated, and only a probe the kernel accepts lets the real file
be created. A swap file on such storage would occupy the memory it is meant to
extend, and the kernel refuses to activate it in any case.

The last line this program prints is one JSON object for the caller:

    {"changed": bool, "skipped_reason": str | null, "error": str | null}

The caller reads the result from that line and never parses the sentences. The
exit code is 0 when the program did its work or decided that it must not, and 1
when a step failed, which is repeated in the error key of the same line.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Commands this program runs. They are its implementation and live here rather
# than in the values module of the task, because nothing else runs them.
SWAP_SHOW_COMMAND: tuple[str, ...] = ("swapon", "--show", "--noheadings")
SWAP_ON_COMMAND: tuple[str, ...] = ("swapon", "{swapfile_path}")
SWAP_OFF_COMMAND: tuple[str, ...] = ("swapoff", "{swapfile_path}")
CREATE_COMMAND: tuple[str, ...] = (
    "fallocate",
    "-l",
    "{size_mb}M",
    "{swapfile_path}",
)
PROBE_CREATE_COMMAND: tuple[str, ...] = (
    "fallocate",
    "-l",
    "{probe_size_kb}K",
    "{swapfile_path}",
)
CHMOD_COMMAND: tuple[str, ...] = ("chmod", "{file_mode}", "{swapfile_path}")
FORMAT_COMMAND: tuple[str, ...] = ("mkswap", "{swapfile_path}")

# Suffix of the probe file. The probe is created next to the swap file, so it
# runs on the filesystem that would hold the swap.
PROBE_FILE_SUFFIX: str = ".probe"

# Byte factors of the size formula: the kernel reports the installed memory in
# kibibytes, the free space is measured in bytes and the file is created in
# mebibytes.
BYTES_PER_KIB: int = 1024
BYTES_PER_MIB: int = 1024 * 1024


class SwapfileError(Exception):
    """A step could not run, with the sentence the caller must show."""


@dataclass(frozen=True)
class Config:
    """Everything the program needs, all of it read from the command line.

    The size formula factors, the file mode, the probe size and the kernel file
    the memory is read from are values of the task and arrive here as arguments;
    the program itself carries no number a caller may want to change.
    """

    swapfile_path: Path
    file_mode: int
    ram_multiplier: float
    ram_extra_mb: int
    disk_fraction: float
    size_tolerance_mb: int
    probe_size_kb: int
    meminfo_path: Path
    meminfo_total_key: str
    command_timeout_seconds: float
    force: bool


@dataclass(frozen=True)
class Outcome:
    """What the program did, as the caller reads it from the result line."""

    changed: bool
    skipped_reason: str | None


def _substituted(command: tuple[str, ...], values: dict[str, str]) -> list[str]:
    """One command with the placeholders of its parts replaced."""

    return [part.format(**values) for part in command]


def _run(
    command: list[str], timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    """Run one command and keep its output for the report.

    A program the machine does not carry is reported like any other failure, so
    the caller sees one sentence instead of a traceback.
    """

    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise SwapfileError(f"{command[0]} is not installed on this machine") from exc


def _failure_sentence(result: subprocess.CompletedProcess[str]) -> str:
    """The sentence a failed command printed, or its exit code."""

    for stream in (result.stderr, result.stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return f"exit code {result.returncode}"


def _require_success(
    result: subprocess.CompletedProcess[str], action: str, success_line: str
) -> None:
    """Turn a failed command into a sentence naming the action that failed."""

    if result.returncode != 0:
        raise SwapfileError(f"{action} failed: {_failure_sentence(result)}")
    print(success_line)


def _read_total_ram_kib(meminfo_path: Path, total_key: str) -> int:
    """Installed RAM in kibibytes from the kernel file of the caller.

    The name of the line is a value of the task, because two tasks read the same
    line; the program carries no name of its own.
    """

    try:
        lines = meminfo_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SwapfileError(f"cannot read {meminfo_path}: {exc}") from exc
    for line in lines:
        if line.startswith(total_key):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1])
    raise SwapfileError(f"{meminfo_path} carries no {total_key} line")


def _free_disk_kib(directory: Path) -> int:
    """Free space of the filesystem that holds the directory, in kibibytes."""

    try:
        free_bytes = shutil.disk_usage(directory).free
    except OSError as exc:
        raise SwapfileError(
            f"cannot read the free space of {directory}: {exc}"
        ) from exc
    return free_bytes // BYTES_PER_KIB


def _calculate_target_size_mb(ram_kib: int, free_disk_kib: int, config: Config) -> int:
    """Swap size in mebibytes: min(RAM * multiplier + extra, free * fraction).

    The RAM term is what the machine asks for and the disk term caps it, so the
    swap never risks filling the disk. The smaller of the two wins.
    """

    ram_mb = ram_kib // BYTES_PER_KIB
    ram_based = int(ram_mb * config.ram_multiplier) + config.ram_extra_mb
    disk_based = int(free_disk_kib // BYTES_PER_KIB * config.disk_fraction)
    return min(ram_based, disk_based)


def _current_size_mb(swapfile_path: Path) -> int | None:
    """Size of the swap file in mebibytes, or None when there is no file."""

    try:
        size = swapfile_path.stat().st_size
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SwapfileError(f"cannot read {swapfile_path}: {exc}") from exc
    return size // BYTES_PER_MIB


def _active_swap_paths(timeout_seconds: float) -> tuple[str, ...]:
    """First column of the active swap listing, one entry per swap device.

    The whole list is read rather than searched for the configured path as a
    text: a path that is the beginning of another one would answer for the other.
    """

    result = _run(list(SWAP_SHOW_COMMAND), timeout_seconds)
    if result.returncode != 0:
        raise SwapfileError(f"cannot list the active swap: {_failure_sentence(result)}")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields:
            paths.append(fields[0])
    return tuple(paths)


def _storage_accepts_swap(config: Config) -> tuple[bool, str]:
    """Whether the filesystem of the swap file can hold swap at all.

    A few kibibytes are allocated next to the swap file, formatted and
    activated, and the answer comes from the kernel; the probe is removed and
    deactivated in every case. This decides before the real size is allocated,
    so storage that keeps its data in memory costs a few kibibytes instead of
    the memory the swap file would occupy.
    """

    probe_path = config.swapfile_path.with_name(
        config.swapfile_path.name + PROBE_FILE_SUFFIX
    )
    print(f"probing the storage with {config.probe_size_kb} KiB at {probe_path}")
    created = False
    activated = False
    values = {
        "swapfile_path": str(probe_path),
        "probe_size_kb": str(config.probe_size_kb),
        "file_mode": f"{config.file_mode:04o}",
    }
    try:
        result = _run(
            _substituted(PROBE_CREATE_COMMAND, values),
            config.command_timeout_seconds,
        )
        if result.returncode != 0:
            return False, (
                f"the storage cannot allocate a file: {_failure_sentence(result)}"
            )
        created = True
        result = _run(
            _substituted(CHMOD_COMMAND, values), config.command_timeout_seconds
        )
        if result.returncode != 0:
            return (
                False,
                f"the storage cannot set a file mode: {_failure_sentence(result)}",
            )
        result = _run(
            _substituted(FORMAT_COMMAND, values), config.command_timeout_seconds
        )
        if result.returncode != 0:
            return False, f"the storage cannot format swap: {_failure_sentence(result)}"
        result = _run(
            _substituted(SWAP_ON_COMMAND, values), config.command_timeout_seconds
        )
        if result.returncode != 0:
            return False, (
                "the kernel refuses to activate swap on this storage: "
                f"{_failure_sentence(result)}"
            )
        activated = True
        result = _run(
            _substituted(SWAP_OFF_COMMAND, values), config.command_timeout_seconds
        )
        if result.returncode != 0:
            return False, (
                f"the probe swap could not be deactivated: {_failure_sentence(result)}"
            )
        activated = False
        return True, ""
    finally:
        if activated:
            _run(_substituted(SWAP_OFF_COMMAND, values), config.command_timeout_seconds)
        if created:
            try:
                probe_path.unlink(missing_ok=True)
                print(f"probe removed: {probe_path}")
            except OSError as exc:
                print(f"the probe file was left in place: {exc}", file=sys.stderr)


def _activate_swap(config: Config) -> None:
    """Activate the existing swap file."""

    print(f"activating swap: swapon {config.swapfile_path}")
    result = _run(
        _substituted(SWAP_ON_COMMAND, {"swapfile_path": str(config.swapfile_path)}),
        config.command_timeout_seconds,
    )
    _require_success(result, "activating the swap file", "swap active")


def _create_swapfile(config: Config, target_mb: int) -> None:
    """Create, format and activate the swap file at the computed size."""

    values = {
        "swapfile_path": str(config.swapfile_path),
        "size_mb": str(target_mb),
        "file_mode": f"{config.file_mode:04o}",
    }
    print(f"creating swapfile: fallocate -l {target_mb}M {config.swapfile_path}")
    result = _run(_substituted(CREATE_COMMAND, values), config.command_timeout_seconds)
    _require_success(
        result, "creating the swap file", f"swapfile created: {target_mb} MiB"
    )
    result = _run(_substituted(CHMOD_COMMAND, values), config.command_timeout_seconds)
    _require_success(result, "setting the file mode", "permissions set")
    result = _run(_substituted(FORMAT_COMMAND, values), config.command_timeout_seconds)
    _require_success(result, "formatting the swap file", "swapfile formatted")
    print(f"activating swap: swapon {config.swapfile_path}")
    result = _run(_substituted(SWAP_ON_COMMAND, values), config.command_timeout_seconds)
    _require_success(result, "activating the swap file", "swap active")


def _deactivate_swap_if_active(config: Config, active: bool) -> None:
    """Deactivate the swap file before it is removed or rewritten."""

    if not active:
        return
    print(f"deactivating swap: swapoff {config.swapfile_path}")
    result = _run(
        _substituted(SWAP_OFF_COMMAND, {"swapfile_path": str(config.swapfile_path)}),
        config.command_timeout_seconds,
    )
    _require_success(result, "deactivating the swap file", "swap deactivated")


def configure_swapfile(config: Config) -> Outcome:
    """Bring the swap file of this machine to the computed size and activate it.

    The target state is the file at the computed size and an active swap; where
    it is already reached nothing changes. The probe runs only when the file has
    to be created, because an active swap file is proof enough that the storage
    can hold swap.
    """

    ram_kib = _read_total_ram_kib(config.meminfo_path, config.meminfo_total_key)
    free_disk_kib = _free_disk_kib(config.swapfile_path.parent)
    target_mb = _calculate_target_size_mb(ram_kib, free_disk_kib, config)
    print(
        f"reading installed memory from {config.meminfo_path}: "
        f"{ram_kib // BYTES_PER_KIB} MiB"
    )
    print(
        f"reading free space on {config.swapfile_path.parent}: "
        f"{free_disk_kib // BYTES_PER_KIB} MiB"
    )
    multiplier_text = str(config.ram_multiplier)
    fraction_text = str(config.disk_fraction)
    print(
        f"calculated target size: min({ram_kib // BYTES_PER_KIB} MiB * "
        f"{multiplier_text} + {config.ram_extra_mb} MiB, "
        f"{free_disk_kib // BYTES_PER_KIB} MiB * {fraction_text}) = "
        f"{target_mb} MiB"
    )
    if target_mb <= 0:
        raise SwapfileError(
            "the computed target size is 0 MiB, so no swap file is created; "
            "check the size values of the section"
        )

    current_mb = _current_size_mb(config.swapfile_path)
    if current_mb is None:
        print(f"checking swapfile {config.swapfile_path}: absent")
    else:
        print(f"checking swapfile {config.swapfile_path}: {current_mb} MiB")
    active = str(config.swapfile_path) in _active_swap_paths(
        config.command_timeout_seconds
    )
    print(f"checking active swap: {'active' if active else 'inactive'}")

    size_is_right = (
        current_mb is not None
        and abs(current_mb - target_mb) <= config.size_tolerance_mb
    )
    if not config.force and size_is_right:
        if active:
            print("target state already reached, nothing to do")
            return Outcome(changed=False, skipped_reason=None)
        _activate_swap(config)
        return Outcome(changed=True, skipped_reason=None)

    if config.force:
        print("force mode, the swap file is created again")
    elif current_mb is None:
        print(f"creating the swap file at the target size {target_mb} MiB")
    else:
        print(
            f"swapfile size {current_mb} MiB differs from the target "
            f"{target_mb} MiB, recreating"
        )

    accepted, reason = _storage_accepts_swap(config)
    if not accepted:
        print(f"the storage cannot hold swap: {reason}")
        print("nothing was created; configure the swap file on a disk filesystem")
        return Outcome(changed=False, skipped_reason=reason)

    _deactivate_swap_if_active(config, active)
    print(f"removing the old swapfile {config.swapfile_path}")
    try:
        config.swapfile_path.unlink(missing_ok=True)
    except OSError as exc:
        raise SwapfileError(f"cannot remove {config.swapfile_path}: {exc}") from exc
    _create_swapfile(config, target_mb)
    return Outcome(changed=True, skipped_reason=None)


def _octal(text: str) -> int:
    """A file mode written the way chmod takes it, for example 600."""

    try:
        return int(text, 8)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text} is not an octal file mode") from exc


def _build_parser() -> argparse.ArgumentParser:
    """The command line of the program: every value the caller owns."""

    parser = argparse.ArgumentParser(
        prog="configure_swapfile",
        description="Ensure the swap file of this machine and activate it.",
    )
    parser.add_argument("--swapfile", required=True, help="path of the swap file")
    parser.add_argument(
        "--file-mode", required=True, type=_octal, help="mode of the swap file"
    )
    parser.add_argument(
        "--ram-multiplier", required=True, type=float, help="factor applied to RAM"
    )
    parser.add_argument(
        "--ram-extra-mb",
        required=True,
        type=int,
        help="mebibytes added to the RAM term",
    )
    parser.add_argument(
        "--disk-fraction", required=True, type=float, help="share of the free disk"
    )
    parser.add_argument(
        "--size-tolerance-mb",
        required=True,
        type=int,
        help="accepted deviation from the target size",
    )
    parser.add_argument(
        "--probe-size-kb",
        required=True,
        type=int,
        help="size of the file the storage is probed with",
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
        "--command-timeout-seconds",
        required=True,
        type=float,
        help="bound of every command this program runs",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="create the swap file again even where the target size is reached",
    )
    return parser


def _config_from_arguments(argv: list[str]) -> Config:
    """Read the command line into the typed configuration of one run."""

    arguments = _build_parser().parse_args(argv)
    return Config(
        swapfile_path=Path(arguments.swapfile),
        file_mode=arguments.file_mode,
        ram_multiplier=arguments.ram_multiplier,
        ram_extra_mb=arguments.ram_extra_mb,
        disk_fraction=arguments.disk_fraction,
        size_tolerance_mb=arguments.size_tolerance_mb,
        probe_size_kb=arguments.probe_size_kb,
        meminfo_path=Path(arguments.meminfo),
        meminfo_total_key=arguments.meminfo_total_key,
        command_timeout_seconds=arguments.command_timeout_seconds,
        force=arguments.force,
    )


def main(argv: list[str]) -> int:
    """Run the program and print its result line for the caller."""

    config = _config_from_arguments(argv)
    try:
        outcome = configure_swapfile(config)
    except (SwapfileError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(json.dumps({"changed": False, "skipped_reason": None, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "changed": outcome.changed,
                "skipped_reason": outcome.skipped_reason,
                "error": None,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
