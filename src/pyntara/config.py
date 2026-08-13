"""Configuration loading from config.toml.

The file at the repository root is the single source of truth for the
Python part of the engine. A missing or invalid file stops the run: there
are no defaults (architecture contract section 3). The composition root
loads the config once and hands it to every task through Context.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when config.toml is missing, unreadable or invalid."""


# The three install modes. They live here rather than in config.toml because
# inst.sh and the desktop detection in the entry point are hard-wired to the
# same names: moving them into the file would create a second source of
# truth for the mode vocabulary.
MODES: tuple[str, ...] = ("minimal", "server", "desktop")

# Allowed values of system_metrics_setup.send_order. The vocabulary is part
# of the config contract and therefore validated here, like MODES.
SEND_ORDERS: tuple[str, ...] = ("oldest_first", "newest_first")

# Allowed values of i2pd_service_setup.log_level. The vocabulary is part of
# the config contract and therefore validated here, like MODES; the values
# are the log levels i2pd accepts in its configuration file.
I2PD_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warn", "error", "none")


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


@dataclass(frozen=True)
class CliToolsConfig:
    """Console utility set installed by the cli_tools task."""

    packages: tuple[str, ...]
    package_status_timeout_seconds: int
    package_install_retries: int
    package_success_threshold_percent: int


@dataclass(frozen=True)
class AddExtraReposConfig:
    """Ubuntu archive components and hosts managed by add_extra_repos.

    components are the archive components ensured in every Ubuntu section;
    ubuntu_hosts are the official archive hosts whose source files the task
    may rewrite. A source file matching none of the hosts is third-party
    and left untouched.
    """

    components: tuple[str, ...]
    ubuntu_hosts: tuple[str, ...]


@dataclass(frozen=True)
class SwapfileServiceInstallConfig:
    """Swap file parameters for the swapfile_service_install task.

    The swap size is min(RAM * ram_multiplier + ram_extra_mb,
    free_disk * disk_fraction); ram_multiplier and ram_extra_mb size the
    swap from installed RAM, disk_fraction caps it by free disk space.
    swapfile_mode is the octal file mode of the created swapfile;
    size_tolerance_mb is the accepted deviation between the existing and
    the target swap size in mebibytes, so a swapfile resized by rounding
    is not recreated. service_unit_name is the name of the systemd
    oneshot service that activates the swap at boot.
    """

    swapfile_path: Path
    ram_multiplier: float
    ram_extra_mb: int
    disk_fraction: float
    swapfile_mode: int
    size_tolerance_mb: int
    service_unit_name: str


@dataclass(frozen=True)
class ZswapServiceConfig:
    """Compressed swap cache parameters for the zswap_service task.

    The values are written into /sys/module/zswap/parameters. enabled and
    shrinker_enabled are strict booleans; compressor names the compression
    algorithm; max_pool_percent and accept_threshold_percent are the pool
    ceiling and the re-accept threshold as percentages of RAM and of the
    pool limit. service_unit_name is the name of the systemd oneshot
    service that repeats the writes at boot.
    """

    enabled: bool
    compressor: str
    max_pool_percent: int
    accept_threshold_percent: int
    shrinker_enabled: bool
    service_unit_name: str


@dataclass(frozen=True)
class ZramServiceConfig:
    """Aggressive in-memory swap parameters for the zram_service task.

    The device count equals the CPU core count (fallback_cpu_count when it
    cannot be determined); the total capacity is memory_fraction_percent of
    installed RAM split evenly across the devices and rounded down to the
    alignment_bytes zram page size. Every device uses the compressor
    algorithm and is activated with swap_priority, so ZRAM swap is
    preferred over the disk swapfile. service_unit_name is the name of the
    systemd oneshot service that repeats the setup at boot.
    reset_busy_attempts and reset_busy_retry_delay_seconds bound the
    retries of a reset or hot_remove rejected with EBUSY while a
    transient opener, for example a udev probe, holds the device.
    """

    compressor: str
    swap_priority: int
    memory_fraction_percent: int
    fallback_cpu_count: int
    alignment_bytes: int
    service_unit_name: str
    reset_busy_attempts: int
    reset_busy_retry_delay_seconds: float


@dataclass(frozen=True)
class I2pdServiceSetupConfig:
    """i2pd installation parameters for the i2pd_service_setup task.

    The task installs the newest i2pd release from github_repo (owner/name)
    as a system service and owns the main configuration file. download_dir
    is the temporary directory for the downloaded package and the checksum
    file; service_unit_name is the systemd unit installed by the package;
    config_path is the main configuration file the task writes, and it must
    match the --conf path of the package unit, otherwise the changes are
    ignored; log_level is the i2pd verbosity from I2PD_LOG_LEVELS;
    http_enabled and socks_proxy_enabled toggle the web console and the
    SOCKS proxy in the rendered configuration; install_retries is the
    retry count of the package install, so the total attempts are retries
    plus one; start_check_attempts and start_check_retry_delay_seconds
    bound the loop that waits for the service to become active after a
    start, because a forking service may take a moment to fork.
    """

    github_repo: str
    download_dir: Path
    service_unit_name: str
    config_path: Path
    log_level: str
    http_enabled: bool
    socks_proxy_enabled: bool
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float


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
class VaultEntry:
    """One entry of the [vault_structure] table.

    title names the KeePass entry; notes carries the explanatory text that
    the regeneration tooling stores in the notes field of the entry.
    """

    title: str
    notes: str


@dataclass(frozen=True)
class VaultStructureConfig:
    """KeePass vault layout described in the [vault_structure] table.

    The table is the single source of truth for the vault structure
    (docs/spec/secrets-model.md): the structure is flat, every entry lives
    in the root group and is identified by its unique title; notes
    explains what the entry carries and who consumes it.
    """

    entries: tuple[VaultEntry, ...]


@dataclass(frozen=True)
class LocalVaultSetupConfig:
    """Runtime secret vault parameters for the local_vault_setup task.

    source_vault_production and source_vault_default are repository-root
    relative paths to the KeePass databases whose copy becomes the runtime
    vault; local_vault_path and pass_file_path are the absolute target
    locations fixed by docs/spec/secrets-model.md; vault_password_entry_title
    names the source vault entry (from the [vault_structure] table) that
    carries the future local vault password.
    """

    source_vault_production: Path
    source_vault_default: Path
    local_vault_path: Path
    pass_file_path: Path
    vault_password_entry_title: str
    secrets_dir_mode: int
    local_vault_file_mode: int
    pass_dir_mode: int
    pass_file_mode: int
    error_priority: int


@dataclass(frozen=True)
class TaskConfig:
    """One task entry from the [[tasks]] section of config.toml."""

    name: str
    description: str
    depends: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    """Validated content of config.toml."""

    engine: EngineConfig
    cli_tools: CliToolsConfig
    add_extra_repos: AddExtraReposConfig
    swapfile_service_install: SwapfileServiceInstallConfig
    zswap_service: ZswapServiceConfig
    zram_service: ZramServiceConfig
    i2pd_service_setup: I2pdServiceSetupConfig
    system_metrics_setup: SystemMetricsSetupConfig
    vault_structure: VaultStructureConfig
    local_vault_setup: LocalVaultSetupConfig
    tasks: tuple[TaskConfig, ...]


def _int_field(raw: object, name: str) -> int:
    """Validate an integer config value; bool is a subclass of int and must
    be excluded explicitly."""

    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ConfigError(f"{name} must be an integer")
    return raw


def _float_field(raw: object, name: str) -> float:
    """Validate a numeric config value; bool is a subclass of int and must
    be excluded explicitly."""

    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigError(f"{name} must be a number")
    value = float(raw)
    if value < 0:
        raise ConfigError(f"{name} must not be negative")
    return value


def _octal_mode_field(raw: object, name: str) -> int:
    """Parse one octal file mode string like "0700" into an int.

    TOML has no octal literals, so the modes are configured as strings
    and converted here; a value that is not four octal digits is a
    config error.
    """

    if not isinstance(raw, str) or len(raw) != 4:
        raise ConfigError(f"{name} must be an octal string like '0700'")
    try:
        parsed = int(raw, 8)
    except ValueError:
        raise ConfigError(f"{name} must be an octal string like '0700'") from None
    return parsed


def _nonempty_string_field(raw: object, name: str) -> str:
    """Validate a non-empty string config value."""

    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{name} must be a non-empty string")
    return raw


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


def _cli_tools_table(raw: object) -> CliToolsConfig:
    """Validate the [cli_tools] table and build CliToolsConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[cli_tools] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("cli_tools.packages must be an array of strings")
    package_success_threshold_percent = _int_field(
        raw.get("package_success_threshold_percent"),
        "cli_tools.package_success_threshold_percent",
    )
    if not 0 <= package_success_threshold_percent <= 100:
        raise ConfigError(
            "cli_tools.package_success_threshold_percent must be between 0 and 100"
        )
    return CliToolsConfig(
        packages=tuple(packages),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "cli_tools.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"), "cli_tools.package_install_retries"
        ),
        package_success_threshold_percent=package_success_threshold_percent,
    )


def _add_extra_repos_table(raw: object) -> AddExtraReposConfig:
    """Validate the [add_extra_repos] table and build AddExtraReposConfig.

    Components are non-empty strings without whitespace, deduplicated while
    preserving their configured order. An empty list is invalid: an empty
    component set would make the task trivially satisfied.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[add_extra_repos] section is missing or not a table")
    components = raw.get("components")
    if not isinstance(components, list) or not components:
        raise ConfigError(
            "add_extra_repos.components must be a non-empty array of strings"
        )
    if not all(
        isinstance(component, str)
        and component
        and component == component.strip()
        and " " not in component
        for component in components
    ):
        raise ConfigError(
            "add_extra_repos.components must be non-empty strings without whitespace"
        )
    unique: list[str] = []
    seen: set[str] = set()
    for component in components:
        if component not in seen:
            seen.add(component)
            unique.append(component)
    ubuntu_hosts = raw.get("ubuntu_hosts")
    if not isinstance(ubuntu_hosts, list) or not ubuntu_hosts:
        raise ConfigError(
            "add_extra_repos.ubuntu_hosts must be a non-empty array of strings"
        )
    if not all(
        isinstance(host, str) and host and host == host.strip()
        for host in ubuntu_hosts
    ):
        raise ConfigError(
            "add_extra_repos.ubuntu_hosts must be non-empty strings"
        )
    return AddExtraReposConfig(components=tuple(unique), ubuntu_hosts=tuple(ubuntu_hosts))


def _swapfile_service_install_table(raw: object) -> SwapfileServiceInstallConfig:
    """Validate the [swapfile_service_install] table and build the config.

    swapfile_path is a non-empty string; ram_multiplier is a non-negative
    number; ram_extra_mb is a non-negative integer; disk_fraction must be
    greater than zero and at most one, so the swap size always stays finite
    and positive when RAM and disk are present. swapfile_mode is an octal
    string like "0600"; size_tolerance_mb is a non-negative integer.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[swapfile_service_install] section is missing or not a table"
        )
    swapfile_path = raw.get("swapfile_path")
    if not isinstance(swapfile_path, str) or not swapfile_path:
        raise ConfigError(
            "swapfile_service_install.swapfile_path must be a non-empty string"
        )
    ram_multiplier = _float_field(
        raw.get("ram_multiplier"), "swapfile_service_install.ram_multiplier"
    )
    ram_extra_mb = _int_field(
        raw.get("ram_extra_mb"), "swapfile_service_install.ram_extra_mb"
    )
    if ram_extra_mb < 0:
        raise ConfigError(
            "swapfile_service_install.ram_extra_mb must not be negative"
        )
    disk_fraction = _float_field(
        raw.get("disk_fraction"), "swapfile_service_install.disk_fraction"
    )
    if not 0 < disk_fraction <= 1:
        raise ConfigError(
            "swapfile_service_install.disk_fraction must be between 0 (exclusive) and 1"
        )
    size_tolerance_mb = _int_field(
        raw.get("size_tolerance_mb"), "swapfile_service_install.size_tolerance_mb"
    )
    if size_tolerance_mb < 0:
        raise ConfigError(
            "swapfile_service_install.size_tolerance_mb must not be negative"
        )
    return SwapfileServiceInstallConfig(
        swapfile_path=Path(swapfile_path),
        ram_multiplier=ram_multiplier,
        ram_extra_mb=ram_extra_mb,
        disk_fraction=disk_fraction,
        swapfile_mode=_octal_mode_field(
            raw.get("swapfile_mode"), "swapfile_service_install.swapfile_mode"
        ),
        size_tolerance_mb=size_tolerance_mb,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"),
            "swapfile_service_install.service_unit_name",
        ),
    )


def _zswap_service_table(raw: object) -> ZswapServiceConfig:
    """Validate the [zswap_service] table and build ZswapServiceConfig.

    enabled and shrinker_enabled are strict booleans; compressor is a
    non-empty string; max_pool_percent and accept_threshold_percent are
    integers between 1 and 100, the meaningful range for a percentage that
    the kernel accepts on the sysfs attributes. A pool ceiling of zero
    would disable zswap entirely, so it is rejected here.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[zswap_service] section is missing or not a table")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise ConfigError("zswap_service.enabled must be a boolean")
    compressor = raw.get("compressor")
    if not isinstance(compressor, str) or not compressor:
        raise ConfigError("zswap_service.compressor must be a non-empty string")
    max_pool_percent = _int_field(
        raw.get("max_pool_percent"), "zswap_service.max_pool_percent"
    )
    if not 1 <= max_pool_percent <= 100:
        raise ConfigError(
            "zswap_service.max_pool_percent must be between 1 and 100"
        )
    accept_threshold_percent = _int_field(
        raw.get("accept_threshold_percent"),
        "zswap_service.accept_threshold_percent",
    )
    if not 1 <= accept_threshold_percent <= 100:
        raise ConfigError(
            "zswap_service.accept_threshold_percent must be between 1 and 100"
        )
    shrinker_enabled = raw.get("shrinker_enabled")
    if not isinstance(shrinker_enabled, bool):
        raise ConfigError("zswap_service.shrinker_enabled must be a boolean")
    return ZswapServiceConfig(
        enabled=enabled,
        compressor=compressor,
        max_pool_percent=max_pool_percent,
        accept_threshold_percent=accept_threshold_percent,
        shrinker_enabled=shrinker_enabled,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "zswap_service.service_unit_name"
        ),
    )


def _zram_service_table(raw: object) -> ZramServiceConfig:
    """Validate the [zram_service] table and build ZramServiceConfig.

    compressor is a non-empty string; swap_priority is a positive swap
    priority; memory_fraction_percent is a percentage between 1 and 100;
    fallback_cpu_count is at least 1; alignment_bytes is positive, because
    the zram driver rejects a non-positive or unaligned disksize;
    reset_busy_attempts is at least 1 and
    reset_busy_retry_delay_seconds is positive.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[zram_service] section is missing or not a table")
    compressor = raw.get("compressor")
    if not isinstance(compressor, str) or not compressor:
        raise ConfigError("zram_service.compressor must be a non-empty string")
    swap_priority = _int_field(
        raw.get("swap_priority"), "zram_service.swap_priority"
    )
    if swap_priority < 1:
        raise ConfigError("zram_service.swap_priority must be positive")
    memory_fraction_percent = _int_field(
        raw.get("memory_fraction_percent"),
        "zram_service.memory_fraction_percent",
    )
    if not 1 <= memory_fraction_percent <= 100:
        raise ConfigError(
            "zram_service.memory_fraction_percent must be between 1 and 100"
        )
    fallback_cpu_count = _int_field(
        raw.get("fallback_cpu_count"), "zram_service.fallback_cpu_count"
    )
    if fallback_cpu_count < 1:
        raise ConfigError("zram_service.fallback_cpu_count must be at least 1")
    alignment_bytes = _int_field(
        raw.get("alignment_bytes"), "zram_service.alignment_bytes"
    )
    if alignment_bytes < 1:
        raise ConfigError("zram_service.alignment_bytes must be positive")
    reset_busy_attempts = _int_field(
        raw.get("reset_busy_attempts"), "zram_service.reset_busy_attempts"
    )
    if reset_busy_attempts < 1:
        raise ConfigError(
            "zram_service.reset_busy_attempts must be at least 1"
        )
    reset_busy_retry_delay_seconds = _float_field(
        raw.get("reset_busy_retry_delay_seconds"),
        "zram_service.reset_busy_retry_delay_seconds",
    )
    if reset_busy_retry_delay_seconds <= 0:
        raise ConfigError(
            "zram_service.reset_busy_retry_delay_seconds must be positive"
        )
    return ZramServiceConfig(
        compressor=compressor,
        swap_priority=swap_priority,
        memory_fraction_percent=memory_fraction_percent,
        fallback_cpu_count=fallback_cpu_count,
        alignment_bytes=alignment_bytes,
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "zram_service.service_unit_name"
        ),
        reset_busy_attempts=reset_busy_attempts,
        reset_busy_retry_delay_seconds=reset_busy_retry_delay_seconds,
    )


def _i2pd_service_setup_table(raw: object) -> I2pdServiceSetupConfig:
    """Validate the [i2pd_service_setup] table and build the config.

    github_repo, download_dir, service_unit_name and config_path are
    non-empty strings; log_level is one of the I2PD_LOG_LEVELS values;
    http_enabled and socks_proxy_enabled are strict booleans;
    install_retries and start_check_attempts are positive integers;
    start_check_retry_delay_seconds is positive, so the readiness loop
    always waits between attempts.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[i2pd_service_setup] section is missing or not a table")
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "i2pd_service_setup.github_repo"
    )
    download_dir = Path(
        _nonempty_string_field(
            raw.get("download_dir"), "i2pd_service_setup.download_dir"
        )
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "i2pd_service_setup.service_unit_name"
    )
    config_path = Path(
        _nonempty_string_field(
            raw.get("config_path"), "i2pd_service_setup.config_path"
        )
    )
    log_level = raw.get("log_level")
    if log_level not in I2PD_LOG_LEVELS:
        raise ConfigError(
            "i2pd_service_setup.log_level must be one of "
            + ", ".join(I2PD_LOG_LEVELS)
        )
    http_enabled = raw.get("http_enabled")
    if not isinstance(http_enabled, bool):
        raise ConfigError("i2pd_service_setup.http_enabled must be a boolean")
    socks_proxy_enabled = raw.get("socks_proxy_enabled")
    if not isinstance(socks_proxy_enabled, bool):
        raise ConfigError(
            "i2pd_service_setup.socks_proxy_enabled must be a boolean"
        )
    install_retries = _int_field(
        raw.get("install_retries"), "i2pd_service_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("i2pd_service_setup.install_retries must be positive")
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "i2pd_service_setup.start_check_attempts"
    )
    if start_check_attempts < 1:
        raise ConfigError(
            "i2pd_service_setup.start_check_attempts must be positive"
        )
    start_check_retry_delay_seconds = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "i2pd_service_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "i2pd_service_setup.start_check_retry_delay_seconds must be positive"
        )
    return I2pdServiceSetupConfig(
        github_repo=github_repo,
        download_dir=download_dir,
        service_unit_name=service_unit_name,
        config_path=config_path,
        log_level=log_level,
        http_enabled=http_enabled,
        socks_proxy_enabled=socks_proxy_enabled,
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
    )


    return I2pdServiceSetupConfig(
        github_repo=github_repo,
        download_dir=download_dir,
        service_unit_name=service_unit_name,
        config_path=config_path,
        log_level=log_level,
        http_enabled=http_enabled,
        socks_proxy_enabled=socks_proxy_enabled,
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
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


def _vault_structure_table(raw: object) -> VaultStructureConfig:
    """Validate the [vault_structure] table and build VaultStructureConfig.

    The section is mandatory and non-empty; every entry is a table with a
    unique non-empty title and a non-empty notes field. The structure is
    flat by contract, so the parser reads entries directly from the table
    and rejects unknown field names: url and any other per-entry value
    live in the vault database, not in the config.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[vault_structure] section is missing or not a table")
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ConfigError(
            "[vault_structure] entries must be a non-empty array of tables"
        )
    entries: list[VaultEntry] = []
    seen_titles: set[str] = set()
    for index, entry_raw in enumerate(entries_raw):
        if not isinstance(entry_raw, dict):
            raise ConfigError("[vault_structure] entries must be tables")
        unknown = sorted(
            name for name in entry_raw if name not in ("title", "notes")
        )
        if unknown:
            raise ConfigError(
                f"[vault_structure] entry {index + 1} names unknown field(s) "
                f"{', '.join(unknown)}; expected title, notes"
            )
        title = entry_raw.get("title")
        if not isinstance(title, str) or not title:
            raise ConfigError(
                "[vault_structure] entry title must be a non-empty string"
            )
        if title in seen_titles:
            raise ConfigError(f"[vault_structure] duplicate entry title: {title}")
        seen_titles.add(title)
        notes = entry_raw.get("notes")
        if not isinstance(notes, str) or not notes:
            raise ConfigError(
                f"[vault_structure] entry {title}: notes must be a non-empty string"
            )
        entries.append(VaultEntry(title=title, notes=notes))
    return VaultStructureConfig(entries=tuple(entries))


def _local_vault_setup_table(raw: object) -> LocalVaultSetupConfig:
    """Validate the [local_vault_setup] table and build the config.

    Source vault paths and the entry title are non-empty strings; the
    source paths are repository-root relative, the target paths absolute
    (the fixed locations from docs/spec/secrets-model.md).
    """

    if not isinstance(raw, dict):
        raise ConfigError("[local_vault_setup] section is missing or not a table")
    source_vault_production = raw.get("source_vault_production")
    if not isinstance(source_vault_production, str) or not source_vault_production:
        raise ConfigError(
            "local_vault_setup.source_vault_production must be a non-empty string"
        )
    source_vault_default = raw.get("source_vault_default")
    if not isinstance(source_vault_default, str) or not source_vault_default:
        raise ConfigError(
            "local_vault_setup.source_vault_default must be a non-empty string"
        )
    local_vault_path = raw.get("local_vault_path")
    if not isinstance(local_vault_path, str) or not local_vault_path:
        raise ConfigError(
            "local_vault_setup.local_vault_path must be a non-empty string"
        )
    pass_file_path = raw.get("pass_file_path")
    if not isinstance(pass_file_path, str) or not pass_file_path:
        raise ConfigError(
            "local_vault_setup.pass_file_path must be a non-empty string"
        )
    vault_password_entry_title = raw.get("vault_password_entry_title")
    if not isinstance(vault_password_entry_title, str) or not vault_password_entry_title:
        raise ConfigError(
            "local_vault_setup.vault_password_entry_title must be a non-empty string"
        )

    def _file_mode_field(name: str) -> int:
        """Parse one octal file mode string like "0700" into an int."""

        return _octal_mode_field(raw.get(name), f"local_vault_setup.{name}")

    error_priority = _int_field(
        raw.get("error_priority"), "local_vault_setup.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "local_vault_setup.error_priority must be between 0 and 7"
        )
    return LocalVaultSetupConfig(
        source_vault_production=Path(source_vault_production),
        source_vault_default=Path(source_vault_default),
        local_vault_path=Path(local_vault_path),
        pass_file_path=Path(pass_file_path),
        vault_password_entry_title=vault_password_entry_title,
        secrets_dir_mode=_file_mode_field("secrets_dir_mode"),
        local_vault_file_mode=_file_mode_field("local_vault_file_mode"),
        pass_dir_mode=_file_mode_field("pass_dir_mode"),
        pass_file_mode=_file_mode_field("pass_file_mode"),
        error_priority=error_priority,
    )


def _tasks_table(raw: object) -> tuple[TaskConfig, ...]:
    """Validate the [[tasks]] section and build the task catalog.

    The catalog is non-empty; names are unique Python identifiers; every
    dependency names a task listed earlier in the file, which also rules out
    dependency cycles and keeps default task sets ordered; modes are known
    install modes without duplicates.
    """

    if not isinstance(raw, list):
        raise ConfigError("[tasks] section is missing or not an array of tables")
    result: list[TaskConfig] = []
    seen_names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError("[tasks] entries must be tables")
        name = entry.get("name")
        if not isinstance(name, str) or not name or not name.isidentifier():
            raise ConfigError("[tasks] task name must be a non-empty identifier")
        if name in seen_names:
            raise ConfigError(f"[tasks] duplicate task name: {name}")
        seen_names.add(name)
        description = entry.get("description")
        if not isinstance(description, str):
            raise ConfigError(f"[tasks] task {name}: description must be a string")
        depends_raw = entry.get("depends", [])
        if not isinstance(depends_raw, list) or not all(
            isinstance(dep, str) for dep in depends_raw
        ):
            raise ConfigError(
                f"[tasks] task {name}: depends must be an array of strings"
            )
        known_names = {task.name for task in result}
        for dep in depends_raw:
            if dep not in known_names:
                raise ConfigError(
                    f"[tasks] task {name}: dependency {dep!r} must be listed earlier"
                )
        modes_raw = entry.get("modes")
        if not isinstance(modes_raw, list) or not modes_raw:
            raise ConfigError(f"[tasks] task {name}: modes must be a non-empty array")
        if not all(isinstance(mode, str) for mode in modes_raw):
            raise ConfigError(f"[tasks] task {name}: modes must be strings")
        for mode in modes_raw:
            if mode not in MODES:
                raise ConfigError(
                    f"[tasks] task {name}: unknown install mode {mode!r}"
                )
        if len(set(modes_raw)) != len(modes_raw):
            raise ConfigError(f"[tasks] task {name}: duplicate mode entries")
        result.append(
            TaskConfig(
                name=name,
                description=description,
                depends=tuple(depends_raw),
                modes=tuple(modes_raw),
            )
        )
    if not result:
        raise ConfigError("[tasks] section must contain at least one task")
    return tuple(result)


def load_config(path: Path) -> Config:
    """Read and validate config.toml. Raises ConfigError on any problem."""

    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    vault_structure = _vault_structure_table(data.get("vault_structure"))
    local_vault_setup = _local_vault_setup_table(data.get("local_vault_setup"))
    system_metrics_setup = _system_metrics_setup_table(
        data.get("system_metrics_setup")
    )
    if not any(
        entry.title == local_vault_setup.vault_password_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "local_vault_setup.vault_password_entry_title must name an entry "
            "of the [vault_structure] table"
        )
    if not any(
        entry.title == system_metrics_setup.google_script_key_entry_title
        for entry in vault_structure.entries
    ):
        raise ConfigError(
            "system_metrics_setup.google_script_key_entry_title must name an "
            "entry of the [vault_structure] table"
        )
    return Config(
        engine=_engine_table(data.get("engine")),
        cli_tools=_cli_tools_table(data.get("cli_tools")),
        add_extra_repos=_add_extra_repos_table(data.get("add_extra_repos")),
        swapfile_service_install=_swapfile_service_install_table(
            data.get("swapfile_service_install")
        ),
        zswap_service=_zswap_service_table(data.get("zswap_service")),
        zram_service=_zram_service_table(data.get("zram_service")),
        i2pd_service_setup=_i2pd_service_setup_table(
            data.get("i2pd_service_setup")
        ),
        system_metrics_setup=system_metrics_setup,
        vault_structure=vault_structure,
        local_vault_setup=local_vault_setup,
        tasks=_tasks_table(data.get("tasks")),
    )
