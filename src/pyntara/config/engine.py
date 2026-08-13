"""[engine] table: engine-wide runtime values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _float_field, _int_field


@dataclass(frozen=True)
class EngineConfig:
    """Engine-wide runtime values from the [engine] table.

    desktop_detect_processes are the process names whose presence marks a
    desktop session in the default mode detection; the list lives here so
    the detection is configurable without code changes.
    """

    task_data_root: Path
    notice_timeout: int
    command_timeout_seconds: int
    process_check_timeout_seconds: int
    task_start_delay_seconds: float
    desktop_detect_processes: tuple[str, ...]


def _engine_table(raw: object) -> EngineConfig:
    """Validate the [engine] table and build EngineConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[engine] section is missing or not a table")
    task_data_root = raw.get("task_data_root")
    if not isinstance(task_data_root, str):
        raise ConfigError("engine.task_data_root must be a string")
    desktop_detect_processes = raw.get("desktop_detect_processes")
    if not isinstance(desktop_detect_processes, list) or not desktop_detect_processes:
        raise ConfigError(
            "engine.desktop_detect_processes must be a non-empty array of strings"
        )
    if not all(
        isinstance(process, str) and process and process == process.strip()
        for process in desktop_detect_processes
    ):
        raise ConfigError(
            "engine.desktop_detect_processes must be non-empty strings"
        )
    return EngineConfig(
        task_data_root=Path(task_data_root),
        notice_timeout=_int_field(raw.get("notice_timeout"), "engine.notice_timeout"),
        command_timeout_seconds=_int_field(
            raw.get("command_timeout_seconds"), "engine.command_timeout_seconds"
        ),
        process_check_timeout_seconds=_int_field(
            raw.get("process_check_timeout_seconds"),
            "engine.process_check_timeout_seconds",
        ),
        task_start_delay_seconds=_float_field(
            raw.get("task_start_delay_seconds"), "engine.task_start_delay_seconds"
        ),
        desktop_detect_processes=tuple(desktop_detect_processes),
    )
