"""[engine] table: engine-wide runtime values."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EngineConfig:
    """Engine-wide runtime values from the [engine] table.

    desktop_detect_processes are the process names whose presence marks a
    desktop session in the default mode detection; the list lives here so
    the detection is configurable without code changes. systemd_unit_dir is
    the one directory the tasks that deploy a systemd unit write it to.
    """

    task_data_root: Path
    systemd_unit_dir: Path
    notice_timeout: int
    command_timeout_seconds: int
    curl_timeout_seconds: int
    curl_download_timeout_seconds: int
    curl_retries: int
    curl_retry_delay_seconds: int
    curl_connect_timeout_seconds: int
    curl_retry_max_time_seconds: int
    error_priority: int
    progress_priority: int
    process_check_timeout_seconds: int
    task_start_delay_seconds: float
    desktop_detect_processes: tuple[str, ...]
