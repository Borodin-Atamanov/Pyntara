"""Shared btrfs facts and file writes of the btrfs sections.

Three sections work on the same filesystem: btrfs_setup readies it,
btrfs_recompress compresses what is already on it and btrfs_points_setup stores
the save point and the work copy in it. They read the same facts about the
machine and write the same kinds of file, so the parsing and the writing live
here once and every section imports them instead of copying the logic
(docs/spec/btrfs-setup.md).

A helper never raises for a tool that answers nothing: it returns None or an
empty result, and the section that asked reports the fact as its own warning,
because a machine that cannot answer must still finish its provisioning.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from string import Template

from pyntara.config_edit import replace_line_by_string
from pyntara.utils import run_command, trim_whitespace

# Suffix of the temporary file an atomic write goes through before it replaces
# the target, so a machine that loses power in the middle of a write keeps the
# old file instead of a truncated one.
TEMPORARY_FILE_SUFFIX: str = ".tmp"

# Words of the btrfs subvolume listing command that separate the fields of a
# line from the path they carry.
SUBVOLUME_PATH_SEPARATOR: str = " path "


@dataclass(frozen=True)
class MountedFilesystem:
    """What findmnt reports about one mounted filesystem."""

    device: str
    filesystem_type: str
    subvolume: str
    options: tuple[str, ...]


def split_source(source: str) -> tuple[str, str]:
    """Split a findmnt source into its device and its mounted subvolume.

    A subvolume mount is reported as /dev/vda2[/@], a plain mount as
    /dev/vda2 or as a pseudo source such as overlay. The subvolume is empty
    when the source carries none.
    """

    if source.endswith("]") and "[" in source:
        device, _, rest = source.partition("[")
        return device, rest[:-1]
    return source, ""


def parse_mount_line(line: str) -> MountedFilesystem | None:
    """Read one findmnt line into the facts of a mounted filesystem.

    None means the line does not carry the three fields the command was asked
    for, which the caller reports as an unanswered query.
    """

    fields = trim_whitespace(line).split()
    if len(fields) != 3:
        return None
    device, subvolume = split_source(fields[0])
    return MountedFilesystem(
        device=device,
        filesystem_type=fields[1],
        subvolume=subvolume,
        options=tuple(option for option in fields[2].split(",") if option),
    )


def read_mounted_filesystem(
    command: Iterable[str], timeout: float
) -> MountedFilesystem | None:
    """Run a findmnt command and read its first line.

    A command that fails or answers nothing gives None, and the caller reports
    it: the section then decides whether the machine has work for it at all.
    """

    try:
        result = run_command(command, timeout=timeout, check=False, capture=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if trim_whitespace(line):
            return parse_mount_line(line)
    return None


def parse_subvolume_paths(text: str) -> tuple[str, ...]:
    """Read the subvolume listing of btrfs into the paths it reports.

    One line of the listing is a record of fields and ends with the path of the
    subvolume, so the path of the line is the text behind the separator. A line
    without it is skipped, which keeps a changed listing from breaking the
    section that asked.
    """

    paths: list[str] = []
    for line in text.splitlines():
        if SUBVOLUME_PATH_SEPARATOR not in line:
            continue
        path = trim_whitespace(line.split(SUBVOLUME_PATH_SEPARATOR, 1)[1])
        if path:
            paths.append(path)
    return tuple(paths)


def read_subvolume_paths(command: Iterable[str], timeout: float) -> tuple[str, ...]:
    """Run a btrfs subvolume listing command and read the paths it reports."""

    try:
        result = run_command(command, timeout=timeout, check=False, capture=True)
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0:
        return ()
    return parse_subvolume_paths(result.stdout)


def parse_read_only_property(text: str) -> bool | None:
    """Read the read-only property of a subvolume.

    The property command answers ro=true or ro=false; anything else is an
    unanswered query, and None carries that answer to the caller.
    """

    answer = trim_whitespace(text)
    if answer == "ro=true":
        return True
    if answer == "ro=false":
        return False
    return None


def read_only_property(command: Iterable[str], timeout: float) -> bool | None:
    """Run the property command of one subvolume and read its answer."""

    try:
        result = run_command(command, timeout=timeout, check=False, capture=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_read_only_property(result.stdout)


def subvolume_exists(command: Iterable[str], timeout: float) -> bool:
    """Answer whether a path is a subvolume of a btrfs filesystem.

    The answer comes from the tool itself: btrfs subvolume show describes a
    subvolume and fails for a plain directory, so a machine that carries a
    directory where a subvolume belongs is told apart from a machine that
    carries the subvolume. Reading the listing instead would need the listing
    to name the subvolume the way the caller expects, and the listing names a
    subvolume relative to its parent when the parent is a subvolume of its own
    (verified on a live machine), which makes such a comparison fragile.
    """

    try:
        result = run_command(command, timeout=timeout, check=False, capture=True)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def failure_text(error: BaseException) -> str:
    """The text of a failed command as a message an operator can act on.

    A command that ran carries the text of its own error, and that text is what
    names the cause on the machine; a command that could not start or that ran
    out of time carries none, so its own description is used. A failure that a
    section reports as its warning is read by the user of the machine, who
    cannot look at the code, so the cause has to travel with the warning.
    """

    if isinstance(error, subprocess.CalledProcessError):
        text = (error.stderr or "").strip()
        if text:
            return text.splitlines()[-1]
        return f"the command answered status {error.returncode}"
    return str(error)


def write_file_atomically(path: Path, content: str, file_mode: int) -> None:
    """Write a file so that it is either the old content or the new one.

    The content goes into a temporary file next to the target and replaces it
    in one step, because a machine that loses power in the middle of an
    in-place write would be left with a truncated file that the next boot or
    the next menu build fails on. The mode is set on the temporary file, so the
    target never appears with a permission of its own.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + TEMPORARY_FILE_SUFFIX)
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(file_mode)
    os.replace(temporary, path)


def write_config_directive(
    path: Path, key: str, directive: str
) -> tuple[bool, str | None]:
    """Replace the line of one setting of a shell style configuration file.

    The line is found by its key, so a commented line or a line that carries
    another value of the same key is replaced by the directive and every other
    line of the file survives untouched. The answer is whether the file
    changed and the reason when it could not be written, which the caller
    reports as its own warning. Two sections own different settings of the same
    file, so the writer lives here instead of in either of them.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"cannot read {path}: {exc}"
    new_text, changed = replace_line_by_string(text, key, directive)
    if not changed:
        return False, None
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write {path}: {exc}"
    return True, None


def render_template(template_text: str, substitutions: dict[str, str]) -> str:
    """Render a shipped template with the values of one machine.

    The templates of the sections use the dollar syntax of string.Template, so
    a body that carries braces of its own, such as a systemd unit or a boot
    entry, renders without escaping them.
    """

    return Template(template_text).substitute(substitutions)
