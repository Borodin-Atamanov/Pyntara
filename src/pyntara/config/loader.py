"""Whole-config assembly: the Config dataclass and load_config."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError
from .add_extra_repos import AddExtraReposConfig, _add_extra_repos_table
from .cli_tools import CliToolsConfig, _cli_tools_table
from .dnsproxy_setup import DnsproxySetupConfig, _dnsproxy_setup_table
from .engine import EngineConfig, _engine_table
from .hostname import HostnameConfig, _hostname_table
from .i2pd_service_setup import I2pdServiceSetupConfig, _i2pd_service_setup_table
from .kde_keyboard_setup import KdeKeyboardSetupConfig, _kde_keyboard_setup_table
from .nextdns_setup_system_wide import (
    NextdnsSetupSystemWideConfig,
    _nextdns_setup_system_wide_table,
)
from .ssh import (
    SshClientSetupConfig,
    SshDaemonSetupConfig,
    _ssh_client_setup_table,
    _ssh_daemon_setup_table,
)
from .swapfile_service_install import (
    SwapfileServiceInstallConfig,
    _swapfile_service_install_table,
)
from .system_metrics_setup import (
    SystemMetricsSetupConfig,
    _system_metrics_setup_table,
)
from .tasks import TaskConfig, _tasks_table
from .tor_setup import TorSetupConfig, _tor_setup_table
from .vault import (
    LocalVaultSetupConfig,
    VaultStructureConfig,
    _local_vault_setup_table,
    _vault_structure_table,
)
from .yggdrasil_service_setup import (
    YggdrasilServiceSetupConfig,
    _yggdrasil_service_setup_table,
)
from .zram_service import ZramServiceConfig, _zram_service_table
from .zswap_service import ZswapServiceConfig, _zswap_service_table


@dataclass(frozen=True)
class Config:
    """Validated content of config.toml."""

    engine: EngineConfig
    cli_tools: CliToolsConfig
    dnsproxy_setup: DnsproxySetupConfig
    add_extra_repos: AddExtraReposConfig
    hostname: HostnameConfig
    kde_keyboard_setup: KdeKeyboardSetupConfig
    swapfile_service_install: SwapfileServiceInstallConfig
    zswap_service: ZswapServiceConfig
    zram_service: ZramServiceConfig
    i2pd_service_setup: I2pdServiceSetupConfig
    yggdrasil_service_setup: YggdrasilServiceSetupConfig
    tor_setup: TorSetupConfig
    ssh_daemon_setup: SshDaemonSetupConfig
    ssh_client_setup: SshClientSetupConfig
    nextdns_setup_system_wide: NextdnsSetupSystemWideConfig
    system_metrics_setup: SystemMetricsSetupConfig
    vault_structure: VaultStructureConfig
    local_vault_setup: LocalVaultSetupConfig
    tasks: tuple[TaskConfig, ...]


def render_config_source(path: Path) -> str:
    """Return the TOML text of the config at path.

    A single file is returned as is; a directory is joined from its *.toml
    files in sorted order. The directory form is the repository layout,
    one file per top-level section; the deployed system config is always
    the joined single file. A path that is neither a file nor a directory
    is a ConfigError.
    """

    if path.is_file():
        return path.read_text(encoding="utf-8")
    if path.is_dir():
        return "\n".join(
            child.read_text(encoding="utf-8")
            for child in sorted(path.glob("*.toml"))
        )
    raise ConfigError(f"config file not found: {path}")


def load_config(path: Path) -> Config:
    """Read and validate the config at path. Raises ConfigError on any
    problem."""

    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(render_config_source(path))
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
        dnsproxy_setup=_dnsproxy_setup_table(data.get("dnsproxy_setup")),
        add_extra_repos=_add_extra_repos_table(data.get("add_extra_repos")),
        hostname=_hostname_table(data.get("hostname")),
        kde_keyboard_setup=_kde_keyboard_setup_table(data.get("kde_keyboard_setup")),
        swapfile_service_install=_swapfile_service_install_table(
            data.get("swapfile_service_install")
        ),
        zswap_service=_zswap_service_table(data.get("zswap_service")),
        zram_service=_zram_service_table(data.get("zram_service")),
        i2pd_service_setup=_i2pd_service_setup_table(
            data.get("i2pd_service_setup")
        ),
        yggdrasil_service_setup=_yggdrasil_service_setup_table(
            data.get("yggdrasil_service_setup")
        ),
        tor_setup=_tor_setup_table(data.get("tor_setup")),
        ssh_daemon_setup=_ssh_daemon_setup_table(data.get("ssh_daemon_setup")),
        ssh_client_setup=_ssh_client_setup_table(data.get("ssh_client_setup")),
        nextdns_setup_system_wide=_nextdns_setup_system_wide_table(
            data.get("nextdns_setup_system_wide")
        ),
        system_metrics_setup=system_metrics_setup,
        vault_structure=vault_structure,
        local_vault_setup=local_vault_setup,
        tasks=_tasks_table(data.get("tasks")),
    )
