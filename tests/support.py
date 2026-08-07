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
    Config,
    EngineConfig,
    LocalVaultSetupConfig,
    SwapfileServiceInstallConfig,
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
    zswap_enabled: bool = True,
    zswap_compressor: str = "zstd",
    zswap_max_pool_percent: int = 50,
    zswap_accept_threshold_percent: int = 100,
    zswap_shrinker_enabled: bool = True,
    zram_compressor: str = "zstd",
    zram_swap_priority: int = 1111,
    zram_memory_fraction_percent: int = 96,
    zram_fallback_cpu_count: int = 8,
    zram_alignment_bytes: int = 4096,
    system_metrics_check_interval_seconds: int = 300,
    system_metrics_python_version: str = "3",
    system_metrics_error_priority: int = 3,
    system_metrics_success_priority: int = 7,
    system_metrics_venv_dir: Path = Path("/usr/local/lib/pyntara/venv"),
    system_metrics_system_config_path: Path = Path("/etc/pyntara/config.toml"),
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
        ("telegram_bot_token", "Telegram bot token for telemetry."),
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
        ),
        zswap_service=ZswapServiceConfig(
            enabled=zswap_enabled,
            compressor=zswap_compressor,
            max_pool_percent=zswap_max_pool_percent,
            accept_threshold_percent=zswap_accept_threshold_percent,
            shrinker_enabled=zswap_shrinker_enabled,
        ),
        zram_service=ZramServiceConfig(
            compressor=zram_compressor,
            swap_priority=zram_swap_priority,
            memory_fraction_percent=zram_memory_fraction_percent,
            fallback_cpu_count=zram_fallback_cpu_count,
            alignment_bytes=zram_alignment_bytes,
        ),
        system_metrics_setup=SystemMetricsSetupConfig(
            check_interval_seconds=system_metrics_check_interval_seconds,
            python_version=system_metrics_python_version,
            error_priority=system_metrics_error_priority,
            success_priority=system_metrics_success_priority,
            venv_dir=system_metrics_venv_dir,
            system_config_path=system_metrics_system_config_path,
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
