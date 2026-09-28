#!/usr/bin/python3
"""Ensure the swap file of this machine and activate it.

This program is deployed by the swapfile_service_install task and is started by
the boot service of that task, so it runs with the system interpreter, imports
the standard library alone and never reaches into the pyntara package: the
deployment venv of this project belongs to a later task, and the boot path must
not depend on it. Every value arrives as a command line argument, so the values
live in the values module of the task and nowhere else; the absolute path of
every tool this program runs is discovered at run time with the standard
library, and the file names it derives are its own implementation.

The program is idempotent. It reads the state of the machine, brings the swap
file to the computed size, formats it and activates it, and where the target
state is already reached it changes nothing. The size is
min(installed RAM * ram_multiplier + ram_extra_mb, free disk * disk_fraction),
so a machine with room to spare gets the swap the RAM asks for and a small disk
never gets a file that fills it.

One recipe serves every filesystem. The directory that holds the swap file is
created, an empty file is made, the no-copy-on-write attribute is asked for and
its refusal is only reported, the size is preallocated without holes, the
declared mode is set and the file is formatted as swap. That order is what a
btrfs swap file requires, because the attribute can be set only while the file
holds no data blocks, and on a filesystem without copy-on-write the same order
holds and only the attribute step is refused.

Storage is refused before the real size is allocated: a probe file of a few
kibibytes is created next to the swap file by the very same recipe, formatted
and activated, and only a probe the kernel accepts lets the real file be
created. A swap file on storage that keeps its data in memory would occupy the
memory it is meant to extend, and the kernel refuses to activate it in any case.

The offset the kernel resumes a hibernation image from is read from the machine
and reported in the result line, because it moves whenever the file is created
again and the caller publishes it for the next boot. The tool that answers it
follows the filesystem type of the swap file: a btrfs swap file is mapped by the
btrfs command, and on a filesystem without copy-on-write the offset names the
first block of the file.

The last line this program prints is one JSON object for the caller:

    {
        "changed": bool,
        "skipped_reason": str | null,
        "error": str | null,
        "resume_offset": int | null,
    }

The caller reads the result from that line and never parses the sentences. The
exit code is 0 when the program did its work or decided that it must not, and 1
when a step failed, which is repeated in the error key of the same line.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Names of the tools this program runs. They are its implementation and live
# here rather than in the values module of the task, because nothing else runs
# them. The absolute path of each one is discovered at run time instead of
# being written down, so a machine that keeps its tools elsewhere is followed
# and a missing tool is answered with the name of that tool.
SWAPON_TOOL: str = "swapon"
SWAPOFF_TOOL: str = "swapoff"
MKSWAP_TOOL: str = "mkswap"
FALLOCATE_TOOL: str = "fallocate"
CHMOD_TOOL: str = "chmod"
CHATTR_TOOL: str = "chattr"
BTRFS_TOOL: str = "btrfs"
FINDMNT_TOOL: str = "findmnt"
FILEFRAG_TOOL: str = "filefrag"
UPDATE_GRUB_TOOL: str = "update-grub"

# Tag of the load entry the augtool program of this program builds. The load
# entry is what makes augtool parse the file named in it with the lens named in
# it, and nothing else, so no other file of the machine is read or written.
AUGEAS_LOAD_TAG: str = "pyntara"

# The no-copy-on-write attribute. A btrfs swap file needs it, and a filesystem
# without copy-on-write answers the request with "Operation not supported", so
# the refusal is reported and the work continues.
NO_COW_ATTRIBUTE: str = "+C"

# Suffix of the probe file. The probe is created next to the swap file, so it
# runs on the filesystem that would hold the swap.
PROBE_FILE_SUFFIX: str = ".probe"

# Filesystem type whose swap file carries its own mapping tool. A btrfs swap
# file is mapped by the btrfs command, and the block numbers the file listing
# reports for it are not the ones the kernel resumes with.
BTRFS_FILESYSTEM_TYPE: str = "btrfs"

# Arguments of the calls that read the resume offset: the mapping of a btrfs
# swap file, the filesystem type that holds a path, and the block listing of a
# file.
BTRFS_MAP_SWAPFILE_ARGUMENTS: tuple[str, ...] = (
    "inspect-internal",
    "map-swapfile",
    "-r",
)
FINDMNT_ARGUMENTS: tuple[str, ...] = (
    "--noheadings",
    "--output",
    "FSTYPE",
    "--target",
)
FILEFRAG_ARGUMENTS: tuple[str, ...] = ("-v",)

# The first word of the file listing line that carries the extent beginning at
# the start of the file, and the word of that line that carries the physical
# block of the extent, counted from zero. The block is written with a trailing
# dot pair that is not part of the number.
FIRST_EXTENT_FIRST_WORD: str = "0:"
FILEFRAG_PHYSICAL_BLOCK_WORD: int = 3

# The source of the mount that holds the swap file, which is how this program
# asks for the device node the kernel attribute takes its number from. A btrfs
# mount is reported with its subvolume in brackets after the device, and the
# attribute wants the device alone.
FINDMNT_SOURCE_ARGUMENTS: tuple[str, ...] = (
    "--noheadings",
    "--output",
    "SOURCE",
    "--target",
)

# The signs a value of a settings line may be written with, and the bracket the
# device of a btrfs mount carries its subvolume in.
QUOTE_SIGNS: tuple[str, ...] = ("'", '"')
SUBVOLUME_OPEN_SIGN: str = "["

# Signs of an augtool string and of the line a printed node is written in.
AUGEAS_ESCAPE_SIGNS: tuple[tuple[str, str], ...] = (("\\", "\\\\"), ('"', '\\"'))
AUGEAS_ASSIGNMENT_SEPARATOR: str = " = "

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
    the program itself carries no number a caller may want to change. The paths
    and the names of the resume address arrive the same way, because the caller
    owns where the address is published and which kernel parameters carry it.
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
    resume_device: str
    grub_default_file_path: Path
    grub_command_line_node: str
    initramfs_resume_file_path: Path
    initramfs_resume_node: str
    augeas_command: tuple[str, ...]
    augeas_lens: str
    resume_device_parameter: str
    resume_offset_parameter: str
    power_resume_file_path: Path
    power_resume_offset_file_path: Path
    update_grub_timeout_seconds: float
    force: bool


@dataclass(frozen=True)
class Outcome:
    """What the program did, as the caller reads it from the result line.

    The resume offset is the number of pages into the device that holds the swap
    file at which the kernel finds a hibernation image; None means the offset
    was not read, which the caller is told by a printed sentence as well.
    """

    changed: bool
    skipped_reason: str | None
    resume_offset_pages: int | None


def _tool_path(tool_name: str) -> str:
    """Absolute path of one tool this program runs.

    The path is discovered at run time instead of being written down, so a
    machine that keeps its tools elsewhere is followed, and a machine without
    the tool is answered with the name of that tool rather than a traceback.
    """

    path = shutil.which(tool_name)
    if path is None:
        raise SwapfileError(f"{tool_name} is not installed on this machine")
    return path


def _swap_show_command() -> list[str]:
    """The listing of the active swap devices."""

    return [_tool_path(SWAPON_TOOL), "--show", "--noheadings"]


def _swap_on_command(swapfile_path: Path) -> list[str]:
    """The command that activates one swap file."""

    return [_tool_path(SWAPON_TOOL), str(swapfile_path)]


def _swap_off_command(swapfile_path: Path) -> list[str]:
    """The command that deactivates one swap file."""

    return [_tool_path(SWAPOFF_TOOL), str(swapfile_path)]


def _no_cow_command(swapfile_path: Path) -> list[str]:
    """The command that asks for the no-copy-on-write attribute."""

    return [_tool_path(CHATTR_TOOL), NO_COW_ATTRIBUTE, str(swapfile_path)]


def _allocate_command(swapfile_path: Path, size_text: str) -> list[str]:
    """The command that preallocates the file without holes."""

    return [_tool_path(FALLOCATE_TOOL), "-l", size_text, str(swapfile_path)]


def _chmod_command(swapfile_path: Path, file_mode: int) -> list[str]:
    """The command that sets the mode of the swap file."""

    return [_tool_path(CHMOD_TOOL), f"{file_mode:04o}", str(swapfile_path)]


def _mkswap_command(swapfile_path: Path) -> list[str]:
    """The command that writes the swap signature into the file."""

    return [_tool_path(MKSWAP_TOOL), str(swapfile_path)]


def _run(
    command: list[str],
    timeout_seconds: float,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one command and keep its output for the report.

    A program the machine does not carry is reported like any other failure, so
    the caller sees one sentence instead of a traceback. The text handed in is
    written to the standard input of the command, which is how the augtool
    program of this program is fed.
    """

    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            input=input_text,
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

    result = _run(_swap_show_command(), timeout_seconds)
    if result.returncode != 0:
        raise SwapfileError(f"cannot list the active swap: {_failure_sentence(result)}")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields:
            paths.append(fields[0])
    return tuple(paths)


def _filesystem_type_of(path: Path, timeout_seconds: float) -> str:
    """Filesystem type of the filesystem that holds a path.

    The type decides which tool answers the resume offset, because the blocks a
    btrfs swap file appears to have are not the blocks the kernel resumes with.
    """

    command = [_tool_path(FINDMNT_TOOL), *FINDMNT_ARGUMENTS, str(path)]
    result = _run(command, timeout_seconds)
    if result.returncode != 0:
        raise SwapfileError(
            f"cannot read the filesystem that holds {path}: "
            f"{_failure_sentence(result)}"
        )
    filesystem_type = result.stdout.strip()
    if not filesystem_type:
        raise SwapfileError(f"the filesystem that holds {path} was not reported")
    return filesystem_type


def _offset_number(text: str, swapfile_path: Path) -> int:
    """A whole number of pages, as the offset tools print it."""

    try:
        return int(text)
    except ValueError as exc:
        raise SwapfileError(
            f"the resume offset of {swapfile_path} is not a whole number: {text}"
        ) from exc


def _btrfs_resume_offset_pages(swapfile_path: Path, timeout_seconds: float) -> int:
    """Resume offset of a swap file on btrfs, in pages.

    The btrfs tool maps the swap area of the file, and the number it prints is
    the one the kernel resumes with.
    """

    command = [
        _tool_path(BTRFS_TOOL),
        *BTRFS_MAP_SWAPFILE_ARGUMENTS,
        str(swapfile_path),
    ]
    result = _run(command, timeout_seconds)
    if result.returncode != 0:
        raise SwapfileError(
            f"cannot read the resume offset of {swapfile_path}: "
            f"{_failure_sentence(result)}"
        )
    return _offset_number(result.stdout.strip(), swapfile_path)


def _filefrag_resume_offset_pages(swapfile_path: Path, timeout_seconds: float) -> int:
    """Resume offset of a swap file on another filesystem, in pages.

    The extent that begins at the start of the file stands on the block the
    offset names, and it is the first line of the listing that starts with the
    logical position zero.
    """

    command = [_tool_path(FILEFRAG_TOOL), *FILEFRAG_ARGUMENTS, str(swapfile_path)]
    result = _run(command, timeout_seconds)
    if result.returncode != 0:
        raise SwapfileError(
            f"cannot read the blocks of {swapfile_path}: {_failure_sentence(result)}"
        )
    for line in result.stdout.splitlines():
        words = line.split()
        if not words or words[0] != FIRST_EXTENT_FIRST_WORD:
            continue
        if len(words) <= FILEFRAG_PHYSICAL_BLOCK_WORD:
            break
        return _offset_number(
            words[FILEFRAG_PHYSICAL_BLOCK_WORD].rstrip("."), swapfile_path
        )
    raise SwapfileError(
        f"the first block of {swapfile_path} was not reported by {FILEFRAG_TOOL}"
    )


def _resume_offset_pages(config: Config) -> int | None:
    """Resume offset of the swap file in pages, or None with the reason printed.

    The offset is read from the machine rather than kept anywhere, because the
    header of the swap file moves whenever the file is created again. A machine
    whose offset cannot be read keeps a working swap file, so the reason is
    printed and the swap work stands; the caller decides what to publish.
    """

    try:
        filesystem_type = _filesystem_type_of(
            config.swapfile_path.parent, config.command_timeout_seconds
        )
        if filesystem_type == BTRFS_FILESYSTEM_TYPE:
            offset = _btrfs_resume_offset_pages(
                config.swapfile_path, config.command_timeout_seconds
            )
        else:
            offset = _filefrag_resume_offset_pages(
                config.swapfile_path, config.command_timeout_seconds
            )
    except (SwapfileError, subprocess.TimeoutExpired) as exc:
        print(f"the resume offset was not read: {exc}", file=sys.stderr)
        return None
    print(f"resume offset of {config.swapfile_path}: {offset} pages")
    return offset


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
    activated = False
    try:
        try:
            _prepare_swap_file(config, probe_path, f"{config.probe_size_kb}K")
        except SwapfileError as exc:
            return False, f"the storage cannot hold a swap file: {exc}"
        result = _run(_swap_on_command(probe_path), config.command_timeout_seconds)
        if result.returncode != 0:
            return False, (
                "the kernel refuses to activate swap on this storage: "
                f"{_failure_sentence(result)}"
            )
        activated = True
        result = _run(_swap_off_command(probe_path), config.command_timeout_seconds)
        if result.returncode != 0:
            return False, (
                f"the probe swap could not be deactivated: {_failure_sentence(result)}"
            )
        activated = False
        return True, ""
    finally:
        if activated:
            _run(_swap_off_command(probe_path), config.command_timeout_seconds)
        if probe_path.exists():
            try:
                probe_path.unlink()
                print(f"probe removed: {probe_path}")
            except OSError as exc:
                print(f"the probe file was left in place: {exc}", file=sys.stderr)


def _ensure_swap_directory(directory: Path) -> None:
    """Create the directory that holds the swap file.

    The free space is read from that directory and both the probe and the real
    file are created inside it, so it exists before either of them.
    """

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SwapfileError(f"cannot create {directory}: {exc}") from exc
    print(f"swap directory ready: {directory}")


def _prepare_swap_file(config: Config, swapfile_path: Path, size_text: str) -> None:
    """Create one swap file by the one recipe and format it.

    The empty file comes first, because the no-copy-on-write attribute can be
    set only while the file holds no data blocks; then the size is preallocated
    without holes, the mode is set and the swap signature is written. A refused
    attribute is a note and never stops the work.
    """

    try:
        swapfile_path.unlink(missing_ok=True)
        swapfile_path.touch()
    except OSError as exc:
        raise SwapfileError(f"cannot create {swapfile_path}: {exc}") from exc
    print(f"empty swapfile created: {swapfile_path}")
    result = _run(_no_cow_command(swapfile_path), config.command_timeout_seconds)
    if result.returncode != 0:
        print(
            "the no-copy-on-write attribute is not supported here "
            f"({_failure_sentence(result)}), continuing"
        )
    result = _run(
        _allocate_command(swapfile_path, size_text), config.command_timeout_seconds
    )
    if result.returncode != 0:
        raise SwapfileError(
            f"allocating {size_text} failed: {_failure_sentence(result)}"
        )
    print(f"swapfile allocated: {swapfile_path} at {size_text}")
    result = _run(
        _chmod_command(swapfile_path, config.file_mode), config.command_timeout_seconds
    )
    _require_success(result, "setting the file mode", "permissions set")
    result = _run(_mkswap_command(swapfile_path), config.command_timeout_seconds)
    _require_success(result, "formatting the swap file", "swapfile formatted")


def _activate_swap(config: Config) -> None:
    """Activate the swap file."""

    print(f"activating swap: {config.swapfile_path}")
    result = _run(
        _swap_on_command(config.swapfile_path), config.command_timeout_seconds
    )
    _require_success(result, "activating the swap file", "swap active")


def _deactivate_swap_if_active(config: Config, active: bool) -> None:
    """Deactivate the swap file before it is removed or rewritten."""

    if not active:
        return
    print(f"deactivating swap: {config.swapfile_path}")
    result = _run(
        _swap_off_command(config.swapfile_path), config.command_timeout_seconds
    )
    _require_success(result, "deactivating the swap file", "swap deactivated")


def _augeas_load_lines(config: Config) -> list[str]:
    """Lines of the augtool program that load the settings file alone.

    The load entry names one file and the lens that parses it, so augtool parses
    and writes that file and nothing else of the machine.
    """

    return [
        f"set /augeas/load/{AUGEAS_LOAD_TAG}/lens {config.augeas_lens}",
        f"set /augeas/load/{AUGEAS_LOAD_TAG}/incl {config.grub_default_file_path}",
        "load",
    ]


def _augeas_string(text: str) -> str:
    """A text written as one string of the augtool program."""

    quoted = text
    for sign, escape in AUGEAS_ESCAPE_SIGNS:
        quoted = quoted.replace(sign, escape)
    return f'"{quoted}"'


def _augeas_command_line_value(config: Config) -> tuple[str | None, str | None]:
    """Value of the kernel command line node, and why it was not read.

    The value arrives the way the settings file writes it, with the quotes of
    that file around the words, because augeas models a shell value that way: the
    tool parses the file and this program changes one node of the parsed tree
    instead of the syntax of the file.
    """

    script = (
        "\n".join(
            (*_augeas_load_lines(config), f"print {config.grub_command_line_node}")
        )
        + "\n"
    )
    result = _run(
        list(config.augeas_command),
        config.command_timeout_seconds,
        input_text=script,
    )
    if result.returncode != 0:
        return None, _failure_sentence(result)
    for line in result.stdout.splitlines():
        node, separator, value = line.partition(AUGEAS_ASSIGNMENT_SEPARATOR)
        if separator and node.strip() == config.grub_command_line_node:
            return value.strip().strip('"'), None
    return None, (
        f"{config.augeas_command[0]} reported no {config.grub_command_line_node}"
    )


def _command_line_with_resume_address(
    config: Config, current_value: str, offset_pages: int
) -> str:
    """The command line value with this section's two words set.

    The words inside the quoting of the file are read and written back and the
    quotes are kept, because they belong to the syntax of that file; every other
    word, the words of the machine and of other sections among them, survives.
    """

    opening = current_value[:1] if current_value[:1] in QUOTE_SIGNS else ""
    closing = current_value[-1:] if current_value[-1:] in QUOTE_SIGNS else ""
    words_text = current_value[len(opening) : len(current_value) - len(closing)]
    owned = (
        f"{config.resume_device_parameter}=",
        f"{config.resume_offset_parameter}=",
    )
    words = [word for word in words_text.split() if not word.startswith(owned)]
    words.append(f"{config.resume_device_parameter}={config.resume_device}")
    words.append(f"{config.resume_offset_parameter}={offset_pages}")
    return f"{opening}{' '.join(words)}{closing}"


def _write_command_line(config: Config, offset_pages: int) -> None:
    """Set the resume address in the settings file of the boot menu.

    Augeas parses the file, this program replaces the value of one node and the
    tool writes the file back, so every other line, every comment and the
    quoting style of the machine survive. The menu is rebuilt only when the value
    changed, so a machine whose address is already published costs the read
    alone.
    """

    current_value, reason = _augeas_command_line_value(config)
    if current_value is None:
        print(f"the resume address was not published: {reason}", file=sys.stderr)
        return
    wanted_value = _command_line_with_resume_address(
        config, current_value, offset_pages
    )
    if wanted_value == current_value:
        print("the boot menu already carries the resume address")
        return
    script = (
        "\n".join(
            (
                *_augeas_load_lines(config),
                f"set {config.grub_command_line_node} {_augeas_string(wanted_value)}",
                "save",
            )
        )
        + "\n"
    )
    result = _run(
        list(config.augeas_command),
        config.command_timeout_seconds,
        input_text=script,
    )
    if result.returncode != 0:
        print(
            f"the resume address was not published: {_failure_sentence(result)}",
            file=sys.stderr,
        )
        return
    print(f"resume address written into {config.grub_default_file_path}")
    _rebuild_boot_menu(config)


def _rebuild_boot_menu(config: Config) -> None:
    """Rebuild the boot menu from the settings file that was just written."""

    command = [_tool_path(UPDATE_GRUB_TOOL)]
    result = _run(command, config.update_grub_timeout_seconds)
    if result.returncode != 0:
        print(
            f"the boot menu was not rebuilt: {_failure_sentence(result)}",
            file=sys.stderr,
        )
        return
    print("boot menu rebuilt")


def _swap_device_node(config: Config) -> str | None:
    """Device node that holds the swap file, as the machine names it."""

    command = [
        _tool_path(FINDMNT_TOOL),
        *FINDMNT_SOURCE_ARGUMENTS,
        str(config.swapfile_path.parent),
    ]
    result = _run(command, config.command_timeout_seconds)
    if result.returncode != 0:
        print(
            "the running kernel was not told the resume device: "
            f"{_failure_sentence(result)}",
            file=sys.stderr,
        )
        return None
    device_node = result.stdout.strip().split(SUBVOLUME_OPEN_SIGN, 1)[0].strip()
    if not device_node:
        print(
            "the running kernel was not told the resume device: "
            f"{FINDMNT_TOOL} reported no device",
            file=sys.stderr,
        )
        return None
    return device_node


def _write_initramfs_resume(config: Config) -> None:
    """Set the resume device in the file the initramfs reads.

    Augeas parses that file and creates it on a machine that does not carry it
    yet, so this program never writes the syntax of a machine settings file
    itself. The file carries the same device the kernel command line carries,
    which is what a machine whose command line a person edited by hand needs.
    """

    if not config.resume_device:
        return
    script = (
        "\n".join(
            (
                f"set /augeas/load/{AUGEAS_LOAD_TAG}/lens {config.augeas_lens}",
                (
                    f"set /augeas/load/{AUGEAS_LOAD_TAG}/incl "
                    f"{config.initramfs_resume_file_path}"
                ),
                "load",
                (
                    f"set {config.initramfs_resume_node} "
                    f"{_augeas_string(config.resume_device)}"
                ),
                "save",
            )
        )
        + "\n"
    )
    result = _run(
        list(config.augeas_command),
        config.command_timeout_seconds,
        input_text=script,
    )
    if result.returncode != 0:
        print(
            "the initramfs resume file was not written: "
            f"{_failure_sentence(result)}",
            file=sys.stderr,
        )
        return
    print(f"initramfs resume file written: {config.initramfs_resume_file_path}")


def _write_power_resume(config: Config, offset_pages: int) -> None:
    """Hand this boot the device and the offset of the active swap file.

    The attribute takes the major and minor number of the device, the way the
    kernel prints them back, and the offset in pages. This is the state the
    initramfs sets at every boot, so writing it here is what makes the session
    that installed the swap file agree with the boots that follow.
    """

    device_node = _swap_device_node(config)
    if device_node is None:
        return
    try:
        device_numbers = os.stat(device_node).st_rdev
    except OSError as exc:
        print(
            "the running kernel was not told the resume device: cannot read "
            f"{device_node}: {exc}",
            file=sys.stderr,
        )
        return
    device_number = f"{os.major(device_numbers)}:{os.minor(device_numbers)}"
    written = (
        (config.power_resume_file_path, device_number, "device"),
        (config.power_resume_offset_file_path, str(offset_pages), "offset"),
    )
    for path, text, what in written:
        try:
            path.write_text(f"{text}\n", encoding="utf-8")
        except OSError as exc:
            print(
                f"the running kernel was not told the resume {what}: cannot "
                f"write {path}: {exc}",
                file=sys.stderr,
            )
            return
    print(f"this boot resumes from {device_number} at offset {offset_pages} pages")


def _publish_resume_address(config: Config, offset_pages: int) -> None:
    """Make the resume address known to the boot and to this session.

    The boot reads the device from the kernel command line of the boot menu and
    from the file the initramfs parses before it mounts anything, and it reads
    the offset from the kernel command line alone; this session takes both from
    the attributes of the running kernel. An empty device means the caller could
    not name the filesystem that holds the swap file, so nothing is published and
    the reason is printed instead of an address that names nothing.
    """

    if not config.resume_device:
        print(
            "the resume address was not published: no device was given",
            file=sys.stderr,
        )
        return
    _write_command_line(config, offset_pages)
    _write_initramfs_resume(config)
    _write_power_resume(config, offset_pages)


def configure_swapfile(config: Config) -> Outcome:
    """Bring the swap file of this machine to the computed size and activate it.

    The target state is the file at the computed size and an active swap; where
    it is already reached nothing changes. The probe runs only when the file has
    to be created, because an active swap file is proof enough that the storage
    can hold swap. The offset of the active file is read last and the address it
    belongs to is published, because the caller hands the same address to the
    kernel at every boot and it moves whenever the file is created again.
    """

    ram_kib = _read_total_ram_kib(config.meminfo_path, config.meminfo_total_key)
    _ensure_swap_directory(config.swapfile_path.parent)
    free_disk_kib = _free_disk_kib(config.swapfile_path.parent)
    target_mb = _calculate_target_size_mb(ram_kib, free_disk_kib, config)
    changed = False
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
        else:
            _activate_swap(config)
            changed = True
    else:
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
            return Outcome(
                changed=False,
                skipped_reason=reason,
                resume_offset_pages=None,
            )

        _deactivate_swap_if_active(config, active)
        _prepare_swap_file(config, config.swapfile_path, f"{target_mb}M")
        _activate_swap(config)
        changed = True

    offset_pages = _resume_offset_pages(config)
    if offset_pages is not None:
        try:
            _publish_resume_address(config, offset_pages)
        except (SwapfileError, subprocess.TimeoutExpired) as exc:
            print(f"the resume address was not published: {exc}", file=sys.stderr)
    return Outcome(
        changed=changed,
        skipped_reason=None,
        resume_offset_pages=offset_pages,
    )


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
        "--resume-device",
        required=True,
        help="device the resume address names, empty to publish nothing",
    )
    parser.add_argument(
        "--grub-default-file",
        required=True,
        help="machine settings file that carries the kernel command line",
    )
    parser.add_argument(
        "--grub-command-line-node",
        required=True,
        help="augeas node that carries the kernel command line",
    )
    parser.add_argument(
        "--initramfs-resume-file",
        required=True,
        help="file the initramfs reads the resume device from",
    )
    parser.add_argument(
        "--initramfs-resume-node",
        required=True,
        help="augeas node of the resume device inside that file",
    )
    parser.add_argument(
        "--augeas-command",
        required=True,
        help="driver that edits the machine settings file, words separated by a space",
    )
    parser.add_argument(
        "--augeas-lens",
        required=True,
        help="lens augeas parses that settings file with",
    )
    parser.add_argument(
        "--resume-device-parameter",
        required=True,
        help="name of the kernel parameter that names the resume device",
    )
    parser.add_argument(
        "--resume-offset-parameter",
        required=True,
        help="name of the kernel parameter that names the resume offset",
    )
    parser.add_argument(
        "--power-resume-file",
        required=True,
        help="kernel attribute that takes the resume device of this boot",
    )
    parser.add_argument(
        "--power-resume-offset-file",
        required=True,
        help="kernel attribute that takes the resume offset of this boot",
    )
    parser.add_argument(
        "--update-grub-timeout-seconds",
        required=True,
        type=float,
        help="bound of the call that rebuilds the boot menu",
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
        resume_device=arguments.resume_device,
        grub_default_file_path=Path(arguments.grub_default_file),
        grub_command_line_node=arguments.grub_command_line_node,
        initramfs_resume_file_path=Path(arguments.initramfs_resume_file),
        initramfs_resume_node=arguments.initramfs_resume_node,
        augeas_command=tuple(arguments.augeas_command.split()),
        augeas_lens=arguments.augeas_lens,
        resume_device_parameter=arguments.resume_device_parameter,
        resume_offset_parameter=arguments.resume_offset_parameter,
        power_resume_file_path=Path(arguments.power_resume_file),
        power_resume_offset_file_path=Path(arguments.power_resume_offset_file),
        update_grub_timeout_seconds=arguments.update_grub_timeout_seconds,
        force=arguments.force,
    )


def main(argv: list[str]) -> int:
    """Run the program and print its result line for the caller."""

    config = _config_from_arguments(argv)
    try:
        outcome = configure_swapfile(config)
    except (SwapfileError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "changed": False,
                    "skipped_reason": None,
                    "error": str(exc),
                    "resume_offset": None,
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
                "resume_offset": outcome.resume_offset_pages,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
