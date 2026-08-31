"""Configuration loading from config.toml.

The loader is split into a package by config section: each *_table parser
and its dataclass live in the module of the section (engine.py,
cli_tools.py, ...), the shared field helpers and the vocabulary constants
live in _fields.py, and loader.py assembles the whole Config. This file
re-exports the public surface, so `from pyntara.config import ...` keeps
working unchanged.

The file at the repository root is the single source of truth for the
Python part of the engine. A missing or invalid file stops the run: there
are no defaults (architecture contract, Configuration). The composition root
loads the config once and hands it to every task through Context.
"""

from __future__ import annotations

from ._fields import (
    DNS_OVER_TLS_VALUES,
    I2PD_LOG_LEVELS,
    MODES,
    SEND_ORDERS,
    TOR_LOG_LEVELS,
    YGGDRASIL_LISTEN_SCHEMES,
    YGGDRASIL_PEER_SCHEMES,
    ConfigError,
)
from .add_extra_repos import AddExtraReposConfig
from .cli_tools import CliToolsConfig
from .dnsproxy_setup import DnsproxySetupConfig
from .engine import EngineConfig
from .hostname import HostnameConfig
from .i2pd_service_setup import I2pdServiceSetupConfig
from .kde_keyboard_setup import KdeKeyboardSetupConfig
from .kde_settings import KConfigRecord, KdeSettingsConfig
from .loader import Config, load_config
from .nextdns_setup_system_wide import NextdnsSetupSystemWideConfig
from .port_forwarding_setup import PortForwardingSetupConfig
from .ssh import SshClientSetupConfig, SshDaemonSetupConfig, SshDirective
from .swapfile_service_install import SwapfileServiceInstallConfig
from .system_metrics_setup import (
    CollectorModuleConfig,
    SystemMetricsCollectorConfig,
    SystemMetricsSetupConfig,
)
from .tasks import TaskConfig
from .three_x_ui_xray_setup import ThreeXuiXraySetupConfig
from .tor_setup import TorSetupConfig
from .vault import LocalVaultSetupConfig, VaultEntry, VaultStructureConfig
from .yggdrasil_service_setup import (
    YggdrasilMulticastInterfaceConfig,
    YggdrasilServiceSetupConfig,
)
from .zram_service import ZramServiceConfig
from .zswap_service import ZswapServiceConfig

__all__ = [
    "DNS_OVER_TLS_VALUES",
    "I2PD_LOG_LEVELS",
    "MODES",
    "SEND_ORDERS",
    "TOR_LOG_LEVELS",
    "YGGDRASIL_LISTEN_SCHEMES",
    "YGGDRASIL_PEER_SCHEMES",
    "AddExtraReposConfig",
    "CliToolsConfig",
    "CollectorModuleConfig",
    "Config",
    "ConfigError",
    "DnsproxySetupConfig",
    "EngineConfig",
    "HostnameConfig",
    "I2pdServiceSetupConfig",
    "KConfigRecord",
    "KdeKeyboardSetupConfig",
    "KdeSettingsConfig",
    "LocalVaultSetupConfig",
    "NextdnsSetupSystemWideConfig",
    "PortForwardingSetupConfig",
    "SshClientSetupConfig",
    "SshDaemonSetupConfig",
    "SshDirective",
    "SwapfileServiceInstallConfig",
    "SystemMetricsCollectorConfig",
    "SystemMetricsSetupConfig",
    "TaskConfig",
    "ThreeXuiXraySetupConfig",
    "TorSetupConfig",
    "VaultEntry",
    "VaultStructureConfig",
    "YggdrasilMulticastInterfaceConfig",
    "YggdrasilServiceSetupConfig",
    "ZramServiceConfig",
    "ZswapServiceConfig",
    "load_config",
]
