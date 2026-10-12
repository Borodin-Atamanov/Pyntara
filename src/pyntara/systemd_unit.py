"""Deploy a systemd unit file and the program such a unit runs.

Several tasks deploy their boot service the same way: they compare a rendered
unit with the deployed one, write the unit file when it changed, deploy the
program next to it and read the one-line JSON result the program prints. The
unit comparison, the two unit writes, the program deploy, the program result
and the failed-state check live here once and the tasks import them, so a
wording or a fix can never drift between the sections (architecture contract,
Configuration).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from pyntara.logger import log_progress
from pyntara.utils import run_command, substituted_command, write_file_atomically


def unit_matches(unit_dir: Path, name: str, expected: str) -> bool:
    """True when the deployed unit file equals the expected content."""

    unit_path = unit_dir / name
    try:
        if not unit_path.is_file():
            return False
        return unit_path.read_text(encoding="utf-8") == expected
    except OSError:
        return False


def write_unit(unit_dir: Path, name: str, content: str) -> None:
    """Write the rendered unit file into the systemd unit directory."""

    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / name).write_text(content, encoding="utf-8")


def write_unit_when_changed(
    unit_dir: Path, name: str, content: str
) -> tuple[bool, str | None]:
    """Write the unit file when it differs from the rendered content.

    Returns (changed, error): changed is False when the deployed file already
    matches, and a failure to write is returned as an error text instead of
    raising, so the caller reports it as a warning of its own.
    """

    path = unit_dir / name
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            log_progress(f"unit file already current: {path}")
            return False, None
        unit_dir.mkdir(parents=True, exist_ok=True)
        write_file_atomically(path, content)
    except OSError as exc:
        return False, f"cannot write the unit file {path}: {exc}"
    log_progress(f"unit file written: {path}")
    return True, None


def deploy_program(
    source: Path, target: Path, file_mode: int
) -> tuple[bool, str | None]:
    """Put the program at its deployed path with the given mode.

    The file is written only when its content differs, so a rerun leaves the
    deployed program alone and reports no change of its own.
    """

    try:
        content = source.read_bytes()
    except OSError as exc:
        return False, f"cannot read the program {source}: {exc}"
    try:
        if target.is_file() and target.read_bytes() == content:
            log_progress(f"program already deployed: {target}")
            return False, None
        write_file_atomically(target, content)
        target.chmod(file_mode)
    except OSError as exc:
        return False, f"cannot deploy the program to {target}: {exc}"
    log_progress(f"program deployed: {target}")
    return True, None


def parse_program_result(stdout: str) -> dict[str, object] | None:
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


def service_is_failed(
    command: tuple[str, ...], substitutions: Mapping[str, str], timeout: float
) -> bool:
    """True when the systemd service is in the failed state."""

    result = run_command(
        substituted_command(command, substitutions),
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0
