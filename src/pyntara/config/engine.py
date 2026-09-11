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
    github_latest_release_url is the endpoint of every release query, a
    template whose {repo} is replaced by the repository of the task.
    system_python is the interpreter of the managed system, used by a task
    that runs an embedded client against the system packages.
    journal_identifier is the name under which the engine mirrors its own
    messages into the system journal; the composition root hands it to the
    journal writer before the first message. root_owner_uid and
    root_owner_gid are the owner the shared apply_owner helper gives
    a file the run creates as root. percent_scale is the scale that turns
    a fraction into a percent, and bytes_per_kib with bytes_per_mib are
    the byte counts of a kibibyte and of a mebibyte. desktop_username is
    the account
    of the desktop user whose live session the run reaches;
    session_environment_command prints that session's environment, one
    KEY=VALUE per line, with {username} replaced by desktop_username;
    session_environment_keys are the session variables the run exports to
    every task and every child process; session_bus_key is the bus variable of
    that session and session_display_keys are its display variables, the two
    groups that decide whether a session counts as live.
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
    github_latest_release_url: str
    github_release_download_url: str
    system_python: str
    journal_identifier: str
    root_owner_uid: int
    root_owner_gid: int
    percent_scale: int
    bytes_per_kib: int
    bytes_per_mib: int
    error_priority: int
    progress_priority: int
    process_check_timeout_seconds: int
    task_start_delay_seconds: float
    desktop_detect_processes: tuple[str, ...]
    desktop_username: str = ""
    session_environment_command: tuple[str, ...] = ()
    session_environment_keys: tuple[str, ...] = ()
    session_bus_key: str = ""
    session_display_keys: tuple[str, ...] = ()
