"""Shared test factories and fakes for the engine test suite.

The Context and Config shapes repeat in every test module. Defining them
once here keeps a change to Config (a new field, a renamed sub-config) a
single edit instead of six. FakeProc replaces the identical subprocess
stub classes that were copied per file. Domain-specific fakes (sysfs
mirrors, disk usage) stay in their own test modules.
"""

from __future__ import annotations

from pathlib import Path

from pyntara.config import (
    AddExtraReposConfig,
    CliToolsConfig,
    CollectorModuleConfig,
    Config,
    EngineConfig,
    I2pdServiceSetupConfig,
    LocalVaultSetupConfig,
    SwapfileServiceInstallConfig,
    SystemMetricsCollectorConfig,
    SystemMetricsSetupConfig,
    TaskConfig,
    VaultEntry,
    VaultStructureConfig,
    ZramServiceConfig,
    ZswapServiceConfig,
)
from pyntara.context import Context


class FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_config(
    *,
    task_data_root: Path = Path("/tmp"),
    notice_timeout: int = 7,
    command_timeout_seconds: int = 1800,
    process_check_timeout_seconds: int = 5,
    task_start_delay_seconds: float = 0.5,
    engine_desktop_detect_processes: tuple[str, ...] = (
        "kwin_wayland",
        "kwin_x11",
        "plasmashell",
        "gnome-shell",
    ),
    cli_tools_packages: tuple[str, ...] = ("mc", "htop"),
    cli_tools_threshold: int = 70,
    cli_tools_retries: int = 3,
    cli_tools_status_timeout: int = 30,
    add_extra_repos_components: tuple[str, ...] = (
        "universe",
        "restricted",
        "multiverse",
    ),
    add_extra_repos_ubuntu_hosts: tuple[str, ...] = (
        "archive.ubuntu.com",
        "security.ubuntu.com",
        "ports.ubuntu.com",
        "old-releases.ubuntu.com",
    ),
    swapfile_path: Path = Path("/swapfile"),
    swapfile_ram_multiplier: float = 2.0,
    swapfile_ram_extra_mb: int = 4096,
    swapfile_disk_fraction: float = 0.5,
    swapfile_mode: int = 0o600,
    swapfile_size_tolerance_mb: int = 1,
    swapfile_service_unit_name: str = "swapfile.service",
    zswap_enabled: bool = True,
    zswap_compressor: str = "zstd",
    zswap_max_pool_percent: int = 50,
    zswap_accept_threshold_percent: int = 100,
    zswap_shrinker_enabled: bool = True,
    zswap_service_unit_name: str = "zswap.service",
    zram_compressor: str = "zstd",
    zram_swap_priority: int = 1111,
    zram_memory_fraction_percent: int = 96,
    zram_fallback_cpu_count: int = 8,
    zram_alignment_bytes: int = 4096,
    zram_service_unit_name: str = "zram.service",
    zram_reset_busy_attempts: int = 5,
    zram_reset_busy_retry_delay_seconds: float = 0.5,
    i2pd_github_repo: str = "PurpleI2P/i2pd",
    i2pd_download_dir: Path = Path("/var/lib/pyntara/i2pd-download"),
    i2pd_service_unit_name: str = "i2pd.service",
    i2pd_config_path: Path = Path("/etc/i2pd/i2pd.conf"),
    i2pd_log_level: str = "warn",
    i2pd_http_enabled: bool = False,
    i2pd_socks_proxy_enabled: bool = True,
    i2pd_install_retries: int = 3,
    i2pd_start_check_attempts: int = 5,
    i2pd_start_check_retry_delay_seconds: float = 0.0,
    system_metrics_backoff_base_seconds: int = 2,
    system_metrics_backoff_multiplier: int = 2,
    system_metrics_backoff_max_seconds: int = 14400,
    system_metrics_python_version: str = "3",
    system_metrics_error_priority: int = 3,
    system_metrics_venv_dir: Path = Path("/usr/local/lib/pyntara/venv"),
    system_metrics_system_config_path: Path = Path("/etc/pyntara/config.toml"),
    system_metrics_command_path: Path = Path("/usr/local/bin/commit_system_metrics"),
    system_metrics_dir: Path = Path("/var/lib/pyntara/metrics"),
    system_metrics_dir_mode: int = 0o700,
    system_metrics_queue_file_mode: int = 0o600,
    system_metrics_max_queue_file_size_bytes: int = 104857600,
    system_metrics_send_order: str = "oldest_first",
    system_metrics_queue_file_suffix_length: int = 12,
    system_metrics_spool_dir: Path = Path("/var/spool/system_metrics"),
    system_metrics_spool_dir_mode: int = 0o1733,
    system_metrics_command_file_mode: int = 0o755,
    system_metrics_service_unit_name: str = "system_metrics.service",
    system_metrics_ingest_service_unit_name: str = "system_metrics-ingest.service",
    system_metrics_ingest_path_unit_name: str = "system_metrics-ingest.path",
    system_metrics_service_journal_identifier: str = "system_metrics",
    system_metrics_commit_journal_identifier: str = "commit_system_metrics",
    system_metrics_main_outbox_dir: str = "main_outbox",
    system_metrics_temp_dir: str = "temp",
    system_metrics_spool_temp_prefix: str = ".commit-",
    system_metrics_queue_link_attempts: int = 5,
    system_metrics_google_script_dir: str = "google_script",
    system_metrics_main_sent_dir: str = "main_sent",
    system_metrics_google_script_timeout_seconds: int = 60,
    system_metrics_google_script_key_entry_title: str = "google_script_key",
    system_metrics_google_script_deployment_url_regex: str = (
        r"^https://script\.google\.com/macros/s/([A-Za-z0-9_-]+)/exec$"
    ),
    system_metrics_collector_boot_delay_seconds: int = 30,
    system_metrics_collector_daily_send_time: str = "12:00:00",
    system_metrics_collector_threshold_percent: int = 50,
    system_metrics_collector_retry_base_seconds: int = 2,
    system_metrics_collector_retry_multiplier: int = 2,
    system_metrics_collector_retry_max_seconds: int = 600,
    system_metrics_collector_command_timeout_seconds: int = 15,
    system_metrics_collector_service_unit_name: str = (
        "system_metrics_collector.service"
    ),
    system_metrics_collector_timer_unit_name: str = (
        "system_metrics_collector.timer"
    ),
    system_metrics_collector_journal_identifier: str = (
        "system_metrics_collector"
    ),
    system_metrics_collector_lock_file_path: Path = Path(
        "/run/pyntara/system_metrics_collector.lock"
    ),
    system_metrics_collector_report_file_name: str = "network.json",
    system_metrics_collector_network_modules: tuple[CollectorModuleConfig, ...] = (),
    system_metrics_collector_system_modules: tuple[CollectorModuleConfig, ...] = (),
    local_vault_source_production: Path = Path("secrets/production.vault"),
    local_vault_source_default: Path = Path("secrets/default.vault"),
    local_vault_path: Path = Path("/var/lib/pyntara/secrets/pyntara.vault"),
    local_vault_pass_file_path: Path = Path("/etc/pyntara/pass"),
    local_vault_entry_title: str = "pyntara_local_vault_password",
    local_vault_secrets_dir_mode: int = 0o700,
    local_vault_file_mode: int = 0o640,
    local_vault_pass_dir_mode: int = 0o700,
    local_vault_pass_file_mode: int = 0o400,
    local_vault_error_priority: int = 3,
    vault_entries: tuple[tuple[str, str], ...] = (
        ("password_salt", "Salt for deterministic password derivation."),
        ("pyntara_local_vault_password", "Password for the runtime secret vault."),
        ("telegram_bot_token", "Telegram bot token for System Metrics."),
        ("google_script_key", "Google Drive web app credentials for System Metrics."),
    ),
    tasks: tuple[TaskConfig, ...] = (),
) -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    return Config(
        engine=EngineConfig(
            task_data_root=task_data_root,
            notice_timeout=notice_timeout,
            command_timeout_seconds=command_timeout_seconds,
            process_check_timeout_seconds=process_check_timeout_seconds,
            task_start_delay_seconds=task_start_delay_seconds,
            desktop_detect_processes=engine_desktop_detect_processes,
        ),
        cli_tools=CliToolsConfig(
            packages=cli_tools_packages,
            package_status_timeout_seconds=cli_tools_status_timeout,
            package_install_retries=cli_tools_retries,
            package_success_threshold_percent=cli_tools_threshold,
        ),
        add_extra_repos=AddExtraReposConfig(
            components=add_extra_repos_components,
            ubuntu_hosts=add_extra_repos_ubuntu_hosts,
        ),
        swapfile_service_install=SwapfileServiceInstallConfig(
            swapfile_path=swapfile_path,
            ram_multiplier=swapfile_ram_multiplier,
            ram_extra_mb=swapfile_ram_extra_mb,
            disk_fraction=swapfile_disk_fraction,
            swapfile_mode=swapfile_mode,
            size_tolerance_mb=swapfile_size_tolerance_mb,
            service_unit_name=swapfile_service_unit_name,
        ),
        zswap_service=ZswapServiceConfig(
            enabled=zswap_enabled,
            compressor=zswap_compressor,
            max_pool_percent=zswap_max_pool_percent,
            accept_threshold_percent=zswap_accept_threshold_percent,
            shrinker_enabled=zswap_shrinker_enabled,
            service_unit_name=zswap_service_unit_name,
        ),
        zram_service=ZramServiceConfig(
            compressor=zram_compressor,
            swap_priority=zram_swap_priority,
            memory_fraction_percent=zram_memory_fraction_percent,
            fallback_cpu_count=zram_fallback_cpu_count,
            alignment_bytes=zram_alignment_bytes,
            service_unit_name=zram_service_unit_name,
            reset_busy_attempts=zram_reset_busy_attempts,
            reset_busy_retry_delay_seconds=zram_reset_busy_retry_delay_seconds,
        ),
        i2pd_service_setup=I2pdServiceSetupConfig(
            github_repo=i2pd_github_repo,
            download_dir=i2pd_download_dir,
            service_unit_name=i2pd_service_unit_name,
            config_path=i2pd_config_path,
            log_level=i2pd_log_level,
            http_enabled=i2pd_http_enabled,
            socks_proxy_enabled=i2pd_socks_proxy_enabled,
            install_retries=i2pd_install_retries,
            start_check_attempts=i2pd_start_check_attempts,
            start_check_retry_delay_seconds=i2pd_start_check_retry_delay_seconds,
        ),
        system_metrics_setup=SystemMetricsSetupConfig(
            backoff_base_seconds=system_metrics_backoff_base_seconds,
            backoff_multiplier=system_metrics_backoff_multiplier,
            backoff_max_seconds=system_metrics_backoff_max_seconds,
            python_version=system_metrics_python_version,
            error_priority=system_metrics_error_priority,
            venv_dir=system_metrics_venv_dir,
            system_config_path=system_metrics_system_config_path,
            command_path=system_metrics_command_path,
            system_metrics_dir=system_metrics_dir,
            system_metrics_dir_mode=system_metrics_dir_mode,
            queue_file_mode=system_metrics_queue_file_mode,
            max_queue_file_size_bytes=system_metrics_max_queue_file_size_bytes,
            send_order=system_metrics_send_order,
            queue_file_suffix_length=system_metrics_queue_file_suffix_length,
            spool_dir=system_metrics_spool_dir,
            spool_dir_mode=system_metrics_spool_dir_mode,
            command_file_mode=system_metrics_command_file_mode,
            service_unit_name=system_metrics_service_unit_name,
            ingest_service_unit_name=system_metrics_ingest_service_unit_name,
            ingest_path_unit_name=system_metrics_ingest_path_unit_name,
            service_journal_identifier=system_metrics_service_journal_identifier,
            commit_journal_identifier=system_metrics_commit_journal_identifier,
            main_outbox_dir=system_metrics_main_outbox_dir,
            temp_dir=system_metrics_temp_dir,
            spool_temp_prefix=system_metrics_spool_temp_prefix,
            queue_link_attempts=system_metrics_queue_link_attempts,
            google_script_dir=system_metrics_google_script_dir,
            main_sent_dir=system_metrics_main_sent_dir,
            google_script_timeout_seconds=system_metrics_google_script_timeout_seconds,
            google_script_key_entry_title=system_metrics_google_script_key_entry_title,
            google_script_deployment_url_regex=(
                system_metrics_google_script_deployment_url_regex
            ),
            collector=SystemMetricsCollectorConfig(
                boot_delay_seconds=system_metrics_collector_boot_delay_seconds,
                daily_send_time=system_metrics_collector_daily_send_time,
                threshold_percent=system_metrics_collector_threshold_percent,
                retry_base_seconds=system_metrics_collector_retry_base_seconds,
                retry_multiplier=system_metrics_collector_retry_multiplier,
                retry_max_seconds=system_metrics_collector_retry_max_seconds,
                command_timeout_seconds=system_metrics_collector_command_timeout_seconds,
                service_unit_name=system_metrics_collector_service_unit_name,
                timer_unit_name=system_metrics_collector_timer_unit_name,
                journal_identifier=system_metrics_collector_journal_identifier,
                lock_file_path=system_metrics_collector_lock_file_path,
                report_file_name=system_metrics_collector_report_file_name,
                network_modules=system_metrics_collector_network_modules,
                system_modules=system_metrics_collector_system_modules,
            ),
        ),
        vault_structure=VaultStructureConfig(
            entries=tuple(
                VaultEntry(title=title, notes=notes)
                for title, notes in vault_entries
            )
        ),
        local_vault_setup=LocalVaultSetupConfig(
            source_vault_production=local_vault_source_production,
            source_vault_default=local_vault_source_default,
            local_vault_path=local_vault_path,
            pass_file_path=local_vault_pass_file_path,
            vault_password_entry_title=local_vault_entry_title,
            secrets_dir_mode=local_vault_secrets_dir_mode,
            local_vault_file_mode=local_vault_file_mode,
            pass_dir_mode=local_vault_pass_dir_mode,
            pass_file_mode=local_vault_pass_file_mode,
            error_priority=local_vault_error_priority,
        ),
        tasks=tasks,
    )


def make_context(
    *,
    install_mode: str = "minimal",
    vault_password: str | None = None,
    vault_source: str | None = None,
    force_tasks: frozenset[str] = frozenset(),
    task_data_root: Path = Path("/tmp"),
    skip_apt_update: bool = False,
    config: Config | None = None,
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return Context(
        install_mode=install_mode,
        vault_password=vault_password,
        vault_source=vault_source,
        force_tasks=force_tasks,
        task_data_root=task_data_root,
        skip_apt_update=skip_apt_update,
        config=config if config is not None else make_config(task_data_root=task_data_root),
    )
