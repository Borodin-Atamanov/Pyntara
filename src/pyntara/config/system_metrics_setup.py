"""[system_metrics_setup] table and its [system_metrics_setup.collector]
sub-table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    SEND_ORDERS,
    ConfigError,
    _int_field,
    _nonempty_string_field,
    _octal_mode_field,
)


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
    commit_system_metrics command file. system_metrics_dir is the root
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


def _daily_time_field(raw: object, name: str) -> str:
    """Validate a time of day "HH:MM" or "HH:MM:SS"; return "HH:MM:SS".

    The normalized form feeds the OnCalendar directive of the collector
    timer directly, so the config may use the short form and the renderer
    never has to guess the seconds.
    """

    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{name} must be a time of day like '12:00' or '12:00:00'")
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ConfigError(f"{name} must be a time of day like '12:00' or '12:00:00'")
    try:
        values = [int(part) for part in parts]
    except ValueError:
        raise ConfigError(f"{name} must be a time of day like '12:00' or '12:00:00'") from None
    hour, minute, second = (
        values[0],
        values[1],
        values[2] if len(values) == 3 else 0,
    )
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ConfigError(f"{name} must be a valid time of day")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _collector_modules_field(
    raw: object, name: str
) -> tuple[CollectorModuleConfig, ...]:
    """Validate one module array of the collector table.

    A missing array means no modules of that kind: an empty network
    module list is valid, because the readiness percentage is then 100
    by construction and only the system modules are collected. Every
    module is a table with a unique non-empty name and a non-empty
    command array of non-empty strings; the command is never a shell
    line.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be an array of tables")
    modules: list[CollectorModuleConfig] = []
    seen_names: set[str] = set()
    for index, module_raw in enumerate(raw):
        if not isinstance(module_raw, dict):
            raise ConfigError(f"{name} must be an array of tables")
        module_name = module_raw.get("name")
        if not isinstance(module_name, str) or not module_name:
            raise ConfigError(f"{name}[{index}] name must be a non-empty string")
        if module_name in seen_names:
            raise ConfigError(f"{name} module names must be unique: {module_name}")
        seen_names.add(module_name)
        command = module_raw.get("command")
        if not isinstance(command, list) or not command:
            raise ConfigError(
                f"{name}[{index}] command must be a non-empty array of strings"
            )
        if not all(isinstance(part, str) and part for part in command):
            raise ConfigError(
                f"{name}[{index}] command must be non-empty strings"
            )
        modules.append(CollectorModuleConfig(name=module_name, command=tuple(command)))
    return tuple(modules)


def _system_metrics_collector_table(raw: object) -> SystemMetricsCollectorConfig:
    """Validate the [system_metrics_setup.collector] table and build the
    config.

    The section is mandatory. boot_delay_seconds is a non-negative
    integer; daily_send_time is a time of day "HH:MM" or "HH:MM:SS"
    normalized to "HH:MM:SS"; threshold_percent is an integer between 0
    and 100; retry_base_seconds is positive, retry_multiplier is at
    least 2 and retry_max_seconds is not below retry_base_seconds;
    command_timeout_seconds is positive; the unit names, the journal
    identifier, the report file name are non-empty strings and
    lock_file_path is a non-empty string. The module arrays are
    optional; every module is a table with a unique non-empty name and a
    non-empty command array of non-empty strings.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[system_metrics_setup.collector] section is missing or not a table"
        )
    boot_delay_seconds = _int_field(
        raw.get("boot_delay_seconds"),
        "system_metrics_setup.collector.boot_delay_seconds",
    )
    if boot_delay_seconds < 0:
        raise ConfigError(
            "system_metrics_setup.collector.boot_delay_seconds must not be negative"
        )
    threshold_percent = _int_field(
        raw.get("threshold_percent"),
        "system_metrics_setup.collector.threshold_percent",
    )
    if not 0 <= threshold_percent <= 100:
        raise ConfigError(
            "system_metrics_setup.collector.threshold_percent must be between 0 and 100"
        )
    retry_base_seconds = _int_field(
        raw.get("retry_base_seconds"),
        "system_metrics_setup.collector.retry_base_seconds",
    )
    if retry_base_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.collector.retry_base_seconds must be positive"
        )
    retry_multiplier = _int_field(
        raw.get("retry_multiplier"),
        "system_metrics_setup.collector.retry_multiplier",
    )
    if retry_multiplier < 2:
        raise ConfigError(
            "system_metrics_setup.collector.retry_multiplier must be at least 2"
        )
    retry_max_seconds = _int_field(
        raw.get("retry_max_seconds"),
        "system_metrics_setup.collector.retry_max_seconds",
    )
    if retry_max_seconds < retry_base_seconds:
        raise ConfigError(
            "system_metrics_setup.collector.retry_max_seconds must be at least "
            "retry_base_seconds"
        )
    command_timeout_seconds = _int_field(
        raw.get("command_timeout_seconds"),
        "system_metrics_setup.collector.command_timeout_seconds",
    )
    if command_timeout_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.collector.command_timeout_seconds must be positive"
        )
    return SystemMetricsCollectorConfig(
        boot_delay_seconds=boot_delay_seconds,
        daily_send_time=_daily_time_field(
            raw.get("daily_send_time"),
            "system_metrics_setup.collector.daily_send_time",
        ),
        threshold_percent=threshold_percent,
        retry_base_seconds=retry_base_seconds,
        retry_multiplier=retry_multiplier,
        retry_max_seconds=retry_max_seconds,
        command_timeout_seconds=command_timeout_seconds,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"),
            "system_metrics_setup.collector.service_unit_name",
        ),
        timer_unit_name=_nonempty_string_field(
            raw.get("timer_unit_name"),
            "system_metrics_setup.collector.timer_unit_name",
        ),
        journal_identifier=_nonempty_string_field(
            raw.get("journal_identifier"),
            "system_metrics_setup.collector.journal_identifier",
        ),
        lock_file_path=Path(
            _nonempty_string_field(
                raw.get("lock_file_path"),
                "system_metrics_setup.collector.lock_file_path",
            )
        ),
        report_file_name=_nonempty_string_field(
            raw.get("report_file_name"),
            "system_metrics_setup.collector.report_file_name",
        ),
        network_modules=_collector_modules_field(
            raw.get("network_modules"),
            "system_metrics_setup.collector.network_modules",
        ),
        system_modules=_collector_modules_field(
            raw.get("system_modules"),
            "system_metrics_setup.collector.system_modules",
        ),
    )


def _system_metrics_setup_table(raw: object) -> SystemMetricsSetupConfig:
    """Validate the [system_metrics_setup] table and build the config.

    backoff_base_seconds and backoff_max_seconds are positive integers
    and backoff_max_seconds is not below backoff_base_seconds;
    backoff_multiplier is an integer of at least 2, so the pause always
    grows. python_version is a non-empty string; error_priority is a
    syslog level between 0 and 7; venv_dir, system_config_path,
    command_path,
    system_metrics_dir, spool_dir and every unit name, journal
    identifier, queue directory name and spool temp prefix are non-empty
    strings; system_metrics_dir_mode, queue_file_mode, spool_dir_mode
    and command_file_mode are octal strings; max_queue_file_size_bytes,
    queue_file_suffix_length, queue_link_attempts and
    google_script_timeout_seconds are positive integers; send_order is
    one of the SEND_ORDERS values; google_script_dir, main_sent_dir and
    google_script_key_entry_title are non-empty strings;
    google_script_deployment_url_regex is a non-empty string that
    compiles as a regular expression with exactly one capture group.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[system_metrics_setup] section is missing or not a table"
        )
    backoff_base_seconds = _int_field(
        raw.get("backoff_base_seconds"),
        "system_metrics_setup.backoff_base_seconds",
    )
    if backoff_base_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.backoff_base_seconds must be positive"
        )
    backoff_multiplier = _int_field(
        raw.get("backoff_multiplier"),
        "system_metrics_setup.backoff_multiplier",
    )
    if backoff_multiplier < 2:
        raise ConfigError(
            "system_metrics_setup.backoff_multiplier must be at least 2"
        )
    backoff_max_seconds = _int_field(
        raw.get("backoff_max_seconds"),
        "system_metrics_setup.backoff_max_seconds",
    )
    if backoff_max_seconds < backoff_base_seconds:
        raise ConfigError(
            "system_metrics_setup.backoff_max_seconds must be at least "
            "backoff_base_seconds"
        )
    python_version = raw.get("python_version")
    if not isinstance(python_version, str) or not python_version:
        raise ConfigError(
            "system_metrics_setup.python_version must be a non-empty string"
        )
    error_priority = _int_field(
        raw.get("error_priority"), "system_metrics_setup.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "system_metrics_setup.error_priority must be between 0 and 7"
        )
    venv_dir = raw.get("venv_dir")
    if not isinstance(venv_dir, str) or not venv_dir:
        raise ConfigError(
            "system_metrics_setup.venv_dir must be a non-empty string"
        )
    system_config_path = raw.get("system_config_path")
    if not isinstance(system_config_path, str) or not system_config_path:
        raise ConfigError(
            "system_metrics_setup.system_config_path must be a non-empty string"
        )
    command_path = raw.get("command_path")
    if not isinstance(command_path, str) or not command_path:
        raise ConfigError(
            "system_metrics_setup.command_path must be a non-empty string"
        )
    system_metrics_dir = raw.get("system_metrics_dir")
    if not isinstance(system_metrics_dir, str) or not system_metrics_dir:
        raise ConfigError(
            "system_metrics_setup.system_metrics_dir must be a non-empty string"
        )
    max_queue_file_size_bytes = _int_field(
        raw.get("max_queue_file_size_bytes"),
        "system_metrics_setup.max_queue_file_size_bytes",
    )
    if max_queue_file_size_bytes < 1:
        raise ConfigError(
            "system_metrics_setup.max_queue_file_size_bytes must be positive"
        )
    send_order = raw.get("send_order")
    if send_order not in SEND_ORDERS:
        raise ConfigError(
            "system_metrics_setup.send_order must be one of "
            + ", ".join(SEND_ORDERS)
        )
    queue_file_suffix_length = _int_field(
        raw.get("queue_file_suffix_length"),
        "system_metrics_setup.queue_file_suffix_length",
    )
    if queue_file_suffix_length < 1:
        raise ConfigError(
            "system_metrics_setup.queue_file_suffix_length must be positive"
        )
    queue_link_attempts = _int_field(
        raw.get("queue_link_attempts"),
        "system_metrics_setup.queue_link_attempts",
    )
    if queue_link_attempts < 1:
        raise ConfigError(
            "system_metrics_setup.queue_link_attempts must be positive"
        )
    google_script_dir = _nonempty_string_field(
        raw.get("google_script_dir"),
        "system_metrics_setup.google_script_dir",
    )
    main_sent_dir = _nonempty_string_field(
        raw.get("main_sent_dir"),
        "system_metrics_setup.main_sent_dir",
    )
    google_script_timeout_seconds = _int_field(
        raw.get("google_script_timeout_seconds"),
        "system_metrics_setup.google_script_timeout_seconds",
    )
    if google_script_timeout_seconds < 1:
        raise ConfigError(
            "system_metrics_setup.google_script_timeout_seconds must be positive"
        )
    google_script_key_entry_title = _nonempty_string_field(
        raw.get("google_script_key_entry_title"),
        "system_metrics_setup.google_script_key_entry_title",
    )
    google_script_deployment_url_regex = raw.get(
        "google_script_deployment_url_regex"
    )
    if (
        not isinstance(google_script_deployment_url_regex, str)
        or not google_script_deployment_url_regex
    ):
        raise ConfigError(
            "system_metrics_setup.google_script_deployment_url_regex must "
            "be a non-empty string"
        )
    try:
        compiled_url_regex = re.compile(google_script_deployment_url_regex)
    except re.error as exc:
        raise ConfigError(
            "system_metrics_setup.google_script_deployment_url_regex is not "
            f"a valid regular expression: {exc}"
        ) from None
    if compiled_url_regex.groups != 1:
        raise ConfigError(
            "system_metrics_setup.google_script_deployment_url_regex must "
            "contain exactly one capture group"
        )
    return SystemMetricsSetupConfig(
        backoff_base_seconds=backoff_base_seconds,
        backoff_multiplier=backoff_multiplier,
        backoff_max_seconds=backoff_max_seconds,
        python_version=python_version,
        error_priority=error_priority,
        venv_dir=Path(venv_dir),
        system_config_path=Path(system_config_path),
        command_path=Path(command_path),
        system_metrics_dir=Path(system_metrics_dir),
        system_metrics_dir_mode=_octal_mode_field(
            raw.get("system_metrics_dir_mode"),
            "system_metrics_setup.system_metrics_dir_mode",
        ),
        queue_file_mode=_octal_mode_field(
            raw.get("queue_file_mode"), "system_metrics_setup.queue_file_mode"
        ),
        max_queue_file_size_bytes=max_queue_file_size_bytes,
        send_order=send_order,
        queue_file_suffix_length=queue_file_suffix_length,
        spool_dir=Path(
            _nonempty_string_field(
                raw.get("spool_dir"), "system_metrics_setup.spool_dir"
            )
        ),
        spool_dir_mode=_octal_mode_field(
            raw.get("spool_dir_mode"), "system_metrics_setup.spool_dir_mode"
        ),
        command_file_mode=_octal_mode_field(
            raw.get("command_file_mode"),
            "system_metrics_setup.command_file_mode",
        ),
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"),
            "system_metrics_setup.service_unit_name",
        ),
        ingest_service_unit_name=_nonempty_string_field(
            raw.get("ingest_service_unit_name"),
            "system_metrics_setup.ingest_service_unit_name",
        ),
        ingest_path_unit_name=_nonempty_string_field(
            raw.get("ingest_path_unit_name"),
            "system_metrics_setup.ingest_path_unit_name",
        ),
        service_journal_identifier=_nonempty_string_field(
            raw.get("service_journal_identifier"),
            "system_metrics_setup.service_journal_identifier",
        ),
        commit_journal_identifier=_nonempty_string_field(
            raw.get("commit_journal_identifier"),
            "system_metrics_setup.commit_journal_identifier",
        ),
        main_outbox_dir=_nonempty_string_field(
            raw.get("main_outbox_dir"),
            "system_metrics_setup.main_outbox_dir",
        ),
        temp_dir=_nonempty_string_field(
            raw.get("temp_dir"), "system_metrics_setup.temp_dir"
        ),
        spool_temp_prefix=_nonempty_string_field(
            raw.get("spool_temp_prefix"),
            "system_metrics_setup.spool_temp_prefix",
        ),
        queue_link_attempts=queue_link_attempts,
        google_script_dir=google_script_dir,
        main_sent_dir=main_sent_dir,
        google_script_timeout_seconds=google_script_timeout_seconds,
        google_script_key_entry_title=google_script_key_entry_title,
        google_script_deployment_url_regex=google_script_deployment_url_regex,
        collector=_system_metrics_collector_table(raw.get("collector")),
    )
