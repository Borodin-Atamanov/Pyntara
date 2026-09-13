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
    one commit call; start_command starts the collector service once after
    provisioning, with {service_unit_name} substituted, and is
    deliberately non-blocking. journal_identifier is the journal
    identifier of the collector service; lock_file_path is the flock lock
    that keeps a second instance from committing; network_modules and
    system_modules are the console commands whose full output forms the
    report, the readiness percentage counting only the network modules
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
    start_command: tuple[str, ...]
    journal_identifier: str
    lock_file_path: Path
    report_file_name: str
    report_file_mode: int
    network_modules: tuple[CollectorModuleConfig, ...]
    system_modules: tuple[CollectorModuleConfig, ...]


# The config keys the deployed collector service reads from the
# [system_metrics_setup.collector] table. A key the collector starts reading
# is added to this list, which lives next to the fields it names: the
# deployed service reports the keys it cannot find in words, so the journal
# of a machine shows config keys and not a Python error. Nothing is judged
# here and no key is required to have a particular shape: the rules of the
# config live in tests/config_checks.py.
COLLECTOR_TABLE_KEYS = (
    "lock_file_path",
    "report_file_name",
    "command_timeout_seconds",
    "network_modules",
    "system_modules",
    "threshold_percent",
    "retry_base_seconds",
    "retry_multiplier",
    "retry_max_seconds",
)


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
    venv_python_relative_path is the interpreter inside the venv, so every
    module that runs the deployed code composes the same path,
    command_path being the system path of the generated
    commit_system_metrics command file, and commit_command is the hand-off
    command that passes one file to the queue, with {command_path} and
    {file} substituted, so the collector service and the final commit task
    run the same argv;
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
    file; spool_dir_permission_mask and command_permission_mask are the
    masks the mode checks compare against, the spool one covering the
    special bits and the command one keeping the permission bits only.
    service_unit_name, ingest_service_unit_name and
    ingest_path_unit_name are the unit file names of the service, the
    ingest oneshot and the path watcher, and unit_template_file_name,
    ingest_unit_template_file_name, ingest_path_template_file_name,
    collector_unit_template_file_name, collector_timer_template_file_name
    and commit_command_template_file_name are the templates the task reads
    from task_data/system_metrics_setup/ of the clone and renders;
    systemctl_daemon_reload_command,
    systemctl_enable_command, systemctl_restart_command and
    systemctl_start_command carry the calls the task makes on those
    units, the unit name being their {unit_name} placeholder;
    service_journal_identifier and
    commit_journal_identifier are the journal identifiers of the
    services and of the commit command; main_outbox_dir and temp_dir
    are the queue directory names; spool_temp_prefix is the prefix of
    the commit command temporary files, which the ingest never moves.
    google_script_dir and main_sent_dir are the queue directory names
    of the Google Drive channel and of the sent archive;
    google_script_timeout_seconds is the curl timeout of the Google
    Drive channel upload, which google_script_upload_command spells out as
    its {timeout_seconds} placeholder together with {file_name} and {key};
    google_script_key_entry_title is the title of
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
    venv_python_relative_path: str
    system_config_path: Path
    command_path: Path
    commit_command: tuple[str, ...]
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
    spool_dir_permission_mask: int
    command_file_mode: int
    command_permission_mask: int
    service_unit_name: str
    ingest_service_unit_name: str
    ingest_path_unit_name: str
    unit_template_file_name: str
    ingest_unit_template_file_name: str
    ingest_path_template_file_name: str
    collector_unit_template_file_name: str
    collector_timer_template_file_name: str
    commit_command_template_file_name: str
    systemctl_daemon_reload_command: tuple[str, ...]
    systemctl_enable_command: tuple[str, ...]
    systemctl_restart_command: tuple[str, ...]
    systemctl_start_command: tuple[str, ...]
    send_service_command: tuple[str, ...]
    ingest_service_command: tuple[str, ...]
    collector_service_command: tuple[str, ...]
    venv_version_command: tuple[str, ...]
    venv_create_command: tuple[str, ...]
    venv_sync_command: tuple[str, ...]
    venv_reinstall_flags: tuple[str, ...]
    service_journal_identifier: str
    commit_journal_identifier: str
    main_outbox_dir: str
    temp_dir: str
    spool_temp_prefix: str
    queue_link_attempts: int
    google_script_dir: str
    main_sent_dir: str
    google_script_timeout_seconds: int
    google_script_upload_command: tuple[str, ...]
    google_script_key_entry_title: str
    google_script_deployment_url_regex: str
    collector: SystemMetricsCollectorConfig


# The config keys the deployed metrics services read from the
# [system_metrics_setup] table, one list per service that reads it. A key a
# service starts reading is added to the list of that service, and the lists
# live next to the fields they name: the deployed service reports the keys it
# cannot find in words, so the journal of a machine shows config keys and not
# a Python error. Nothing is judged here and no key is required to have a
# particular shape: the rules of the config live in tests/config_checks.py.
# The send loop stops when a key is absent instead of repeating a failure it
# can never get past.
SERVICE_CONFIG_KEYS = (
    "backoff_base_seconds",
    "backoff_multiplier",
    "backoff_max_seconds",
    "error_priority",
    "system_metrics_dir",
    "system_metrics_dir_mode",
    "main_outbox_dir",
    "google_script_dir",
    "main_sent_dir",
    "send_order",
    "max_queue_file_size_bytes",
    "queue_file_suffix_length",
    "google_script_key_entry_title",
    "google_script_timeout_seconds",
)
INGEST_CONFIG_KEYS = (
    "spool_dir",
    "spool_temp_prefix",
    "system_metrics_dir",
    "system_metrics_dir_mode",
    "main_outbox_dir",
    "temp_dir",
    "max_queue_file_size_bytes",
    "queue_file_mode",
    "queue_file_suffix_length",
    "queue_link_attempts",
)
COLLECTOR_SECTION_KEYS = ("commit_command", "command_path", "error_priority")
