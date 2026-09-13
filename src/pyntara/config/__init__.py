"""Configuration reading from config.toml.

The package is split by config section: each section module holds its
frozen dataclass, the install mode vocabulary lives in _fields.py, and
loader.py reads the document into the Config with the runtime reader. This
file re-exports the public surface, so `from pyntara.config import ...`
keeps working unchanged.

The config/ directory at the repository root is the single source of truth
for the Python part of the engine. Reading is total: a missing file, broken
TOML, an unknown section or key, an absent value and a value of an
unexpected type never stop the run, and no value is invented. No rule of the
config is checked while the run works; every rule lives in the test suite
(tests/config_checks.py, applied to the shipped config/ directory by
tests/test_config_coverage.py), so a broken config fails during development
and never on a machine (architecture contract, Configuration). The
composition root reads the config once and hands it to every task through
Context.
"""

from __future__ import annotations

from ._fields import MODES
from .add_extra_repos import AddExtraReposConfig
from .chrome_setup import ChromeSetupConfig
from .cli_tools import CliToolsConfig
from .dnsproxy_setup import DnsproxySetupConfig
from .engine import EngineConfig
from .ffmpeg_setup import FfmpegSetupConfig
from .hostname import HostnameConfig
from .i2pd_service_setup import I2pdServiceSetupConfig
from .imagemagick_setup import ImagemagickSetupConfig
from .kde_keyboard_setup import KdeKeyboardSetupConfig
from .kde_settings import KConfigRecord, KdeSettingsConfig
from .loader import (
    Config,
    absent_config_keys,
    describe_absent_config_keys,
    load_config,
)
from .nextdns_setup_system_wide import NextdnsSetupSystemWideConfig
from .playwright_setup import PlaywrightSetupConfig
from .port_forwarding_setup import PortForwardingSetupConfig
from .rustdesk_setup import RustdeskOptionConfig, RustdeskSetupConfig
from .ssh import SshClientSetupConfig, SshDaemonSetupConfig, SshDirective
from .swapfile_service_install import SwapfileServiceInstallConfig
from .system_metrics_setup import (
    COLLECTOR_SECTION_KEYS,
    COLLECTOR_TABLE_KEYS,
    INGEST_CONFIG_KEYS,
    SERVICE_CONFIG_KEYS,
    CollectorModuleConfig,
    SystemMetricsCollectorConfig,
    SystemMetricsSetupConfig,
)
from .tasks import TaskConfig
from .telegram_setup import TelegramSetupConfig
from .three_x_ui_xray_setup import ThreeXuiXraySetupConfig
from .tor_setup import TorSetupConfig
from .vault import (
    LocalVaultSetupConfig,
    VaultEntry,
    VaultGroup,
    VaultGroupSeed,
    VaultStructureConfig,
)
from .vocalinux_setup import VocalinuxSetupConfig
from .yggdrasil_service_setup import (
    YggdrasilMulticastInterfaceConfig,
    YggdrasilServiceSetupConfig,
)
from .zram_service import ZramServiceConfig
from .zswap_service import ZswapServiceConfig

__all__ = [
    "COLLECTOR_SECTION_KEYS",
    "COLLECTOR_TABLE_KEYS",
    "INGEST_CONFIG_KEYS",
    "MODES",
    "SERVICE_CONFIG_KEYS",
    "AddExtraReposConfig",
    "ChromeSetupConfig",
    "CliToolsConfig",
    "CollectorModuleConfig",
    "Config",
    "DnsproxySetupConfig",
    "EngineConfig",
    "FfmpegSetupConfig",
    "HostnameConfig",
    "I2pdServiceSetupConfig",
    "ImagemagickSetupConfig",
    "KConfigRecord",
    "KdeKeyboardSetupConfig",
    "KdeSettingsConfig",
    "LocalVaultSetupConfig",
    "NextdnsSetupSystemWideConfig",
    "PlaywrightSetupConfig",
    "PortForwardingSetupConfig",
    "RustdeskOptionConfig",
    "RustdeskSetupConfig",
    "SshClientSetupConfig",
    "SshDaemonSetupConfig",
    "SshDirective",
    "SwapfileServiceInstallConfig",
    "SystemMetricsCollectorConfig",
    "SystemMetricsSetupConfig",
    "TaskConfig",
    "TelegramSetupConfig",
    "ThreeXuiXraySetupConfig",
    "TorSetupConfig",
    "VaultEntry",
    "VaultGroup",
    "VaultGroupSeed",
    "VaultStructureConfig",
    "VocalinuxSetupConfig",
    "YggdrasilMulticastInterfaceConfig",
    "YggdrasilServiceSetupConfig",
    "ZramServiceConfig",
    "ZswapServiceConfig",
    "absent_config_keys",
    "describe_absent_config_keys",
    "load_config",
]
