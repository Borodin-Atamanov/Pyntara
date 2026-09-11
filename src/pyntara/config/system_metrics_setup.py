"""[system_metrics_setup] table and its [system_metrics_setup.collector]
sub-table."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollectorModuleConfig:
    """One console command of the report collector.

    name identifies the module in the report; command is the argv of the
    command without a shell, so no command line is ever interpreted.
    The collector runs the command, keeps its full output and classifies
    the result as ok, empty or error
    (docs/spec/system-metrics.md, section Report collector).
    """

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class SystemMetricsCollectorConfig:
    """Report collector parameters from [system_metrics_setup.collector].

    The collector is a producer of the System Metrics queue: the systemd
    timer (timer_unit_name) starts the oneshot service
    (service_unit_name) after boot and at daily_send_time every day; the
    service runs the configured console commands, keeps their full
    output, waits up to the retry window for threshold_percent of the
    network modules to answer, writes the report as report_file_name and
    commits it through the commit_system_metrics command. All waiting
    happens inside the service: boot_delay_seconds only sets the OnBootSec
    of the timer; retry_base_seconds, retry_multiplier and
    retry_max_seconds are the geometric backoff of the retries, in whole
    seconds; command_timeout_seconds bounds a single console command and
    one commit call. journal_identifier is the journal identifier of the
    collector service; lock_file_path is the flock lock that keeps a
    second instance from committing; network_modules and system_modules
    are the console commands whose full output forms the report, the
    readiness percentage counting only the network modules
    (docs/spec/system-metrics.md, section Report collector).
    """

    boot_delay_seconds: int
    daily_send_time: str
    threshold_percent: int
    retry_base_seconds: int
    retry_multiplier: int
    retry_max_seconds: int
    command_timeout_seconds: int
    service_unit_name: str
    timer_unit_name: str
    journal_identifier: str
    lock_file_path: Path
    report_file_name: str
    report_file_mode: int
    network_modules: tuple[CollectorModuleConfig, ...]
    system_modules: tuple[CollectorModuleConfig, ...]


@dataclass(frozen=True)
class SystemMetricsSetupConfig:
    """Runtime parameters of the long-running System Metrics service.

    The section is read by the deployed service on the target machine
    through pyntara.config.load_config, the same loader the installer
    uses: system_config_path is the single config of the system.
    backoff_base_seconds, backoff_multiplier and
    backoff_max_seconds are the retry mode parameters of the send loop:
    the first failed cycle waits backoff_base_seconds, every further
    consecutive failure multiplies the pause by backoff_multiplier until
    backoff_max_seconds (docs/spec/system-metrics.md, section Schedule
    and retry); python_version selects the interpreter for the deployed
    venv; error_priority is the syslog level of a failed vault open by
    the senders; venv_dir, system_config_path and
    command_path are the deployment locations on the target machine,
    command_path being the system path of the generated
    commit_system_metrics command file;
    vault_backup_file_name is the committed artifact name of the runtime
    vault backup, with {hostname} replaced by the machine hostname at
    commit time. system_metrics_dir is the root
    of the System Metrics queue, system_metrics_dir_mode and
    queue_file_mode are the strict file modes of the queue directories
    and entries, max_queue_file_size_bytes is the per-entry size limit,
    send_order is the drain order of the senders,
    queue_file_suffix_length is the length of the random name suffix and
    queue_link_attempts is the number of publication attempts before the
    ingest gives up on a unique queue name
    (docs/spec/system-metrics.md, section Queue architecture). The
    spool is the intake pre-queue: spool_dir is the directory where the
    generated commit_system_metrics command publishes files, its mode
    spool_dir_mode is 1733 (sticky, write and search for everyone, no
    listing) and command_file_mode is the mode of the generated command
    file. service_unit_name, ingest_service_unit_name and
    ingest_path_unit_name are the unit file names of the service, the
    ingest oneshot and the path watcher; service_journal_identifier and
    commit_journal_identifier are the journal identifiers of the
    services and of the commit command; main_outbox_dir and temp_dir
    are the queue directory names; spool_temp_prefix is the prefix of
    the commit command temporary files, which the ingest never moves.
    google_script_dir and main_sent_dir are the queue directory names
    of the Google Drive channel and of the sent archive;
    google_script_timeout_seconds is the curl timeout of the Google
    Drive channel upload; google_script_key_entry_title is the title of
    the vault entry that carries the web app credentials;
    google_script_deployment_url_regex is the Python regular expression
    of the web app deployment URL, whose single capture group yields the
    deployment ID. The encrypted PDF generation and the Telegram
    channel replace the current Google-only sending in a later stage
    (docs/spec/system-metrics.md).
    """

    backoff_base_seconds: int
    backoff_multiplier: int
    backoff_max_seconds: int
    python_version: str
    error_priority: int
    venv_dir: Path
    system_config_path: Path
    command_path: Path
    vault_backup_file_name: str
    vault_backup_file_mode: int
    system_metrics_dir: Path
    system_metrics_dir_mode: int
    queue_file_mode: int
    max_queue_file_size_bytes: int
    send_order: str
    queue_file_suffix_length: int
    spool_dir: Path
    spool_dir_mode: int
    command_file_mode: int
    service_unit_name: str
    ingest_service_unit_name: str
    ingest_path_unit_name: str
    service_journal_identifier: str
    commit_journal_identifier: str
    main_outbox_dir: str
    temp_dir: str
    spool_temp_prefix: str
    queue_link_attempts: int
    google_script_dir: str
    main_sent_dir: str
    google_script_timeout_seconds: int
    google_script_key_entry_title: str
    google_script_deployment_url_regex: str
    collector: SystemMetricsCollectorConfig
