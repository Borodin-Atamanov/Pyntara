"""Config reading: the Config dataclass and load_config.

The reader is total by design. It reads the document, builds every section
from the keys that are there and returns. A missing file, an unreadable
file, broken TOML, an unknown section, an unknown key or a value of an
unexpected type never stops the run: a key that is not in the document
leaves its field without a value, the task that needed it reports what it
could not do, and the run continues (architecture contract, Configuration).

Nothing is checked here and no value is invented here. The strict checks of
the config live in the test suite, which validates the shipped config/
directory during development, so the target machine only reads.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

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
from .kde_settings import KdeSettingsConfig
from .nextdns_setup_system_wide import NextdnsSetupSystemWideConfig
from .playwright_setup import PlaywrightSetupConfig
from .port_forwarding_setup import PortForwardingSetupConfig
from .rustdesk_setup import RustdeskSetupConfig
from .ssh import SshClientSetupConfig, SshDaemonSetupConfig
from .swapfile_service_install import SwapfileServiceInstallConfig
from .system_metrics_setup import SystemMetricsSetupConfig
from .tasks import TaskConfig
from .telegram_setup import TelegramSetupConfig
from .three_x_ui_xray_setup import ThreeXuiXraySetupConfig
from .tor_setup import TorSetupConfig
from .vault import LocalVaultSetupConfig, VaultStructureConfig
from .vocalinux_setup import VocalinuxSetupConfig
from .yggdrasil_service_setup import YggdrasilServiceSetupConfig
from .zram_service import ZramServiceConfig
from .zswap_service import ZswapServiceConfig


@dataclass(frozen=True)
class Config:
    """Content of the config document, value by value as it was read.

    Every field holds the value of the key with its name, or None when the
    key is not in the document. The field names are the description of what
    the code reads from the config.
    """

    engine: EngineConfig
    cli_tools: CliToolsConfig
    chrome_setup: ChromeSetupConfig
    dnsproxy_setup: DnsproxySetupConfig
    add_extra_repos: AddExtraReposConfig
    hostname: HostnameConfig
    ffmpeg_setup: FfmpegSetupConfig
    imagemagick_setup: ImagemagickSetupConfig
    kde_keyboard_setup: KdeKeyboardSetupConfig
    kde_settings: KdeSettingsConfig
    swapfile_service_install: SwapfileServiceInstallConfig
    zswap_service: ZswapServiceConfig
    zram_service: ZramServiceConfig
    telegram_setup: TelegramSetupConfig
    i2pd_service_setup: I2pdServiceSetupConfig
    yggdrasil_service_setup: YggdrasilServiceSetupConfig
    three_x_ui_xray_setup: ThreeXuiXraySetupConfig
    tor_setup: TorSetupConfig
    ssh_daemon_setup: SshDaemonSetupConfig
    ssh_client_setup: SshClientSetupConfig
    vocalinux_setup: VocalinuxSetupConfig
    nextdns_setup_system_wide: NextdnsSetupSystemWideConfig
    playwright_setup: PlaywrightSetupConfig
    port_forwarding_setup: PortForwardingSetupConfig
    rustdesk_setup: RustdeskSetupConfig
    system_metrics_setup: SystemMetricsSetupConfig
    vault_structure: VaultStructureConfig
    local_vault_setup: LocalVaultSetupConfig
    tasks: tuple[TaskConfig, ...]


def render_config_source(path: Path) -> str:
    """Return the TOML text of the config at path.

    A file is returned as it is; a directory is joined from its *.toml files
    in sorted order, which is the repository layout, one file per top-level
    section. A path that is neither yields an empty document, so the run
    continues with every value absent instead of stopping.
    """

    if path.is_file():
        return path.read_text(encoding="utf-8")
    if path.is_dir():
        return "\n".join(
            child.read_text(encoding="utf-8")
            for child in sorted(path.glob("*.toml"))
        )
    return ""


def _build_value(field_type: object, raw: object) -> Any:
    """Return one raw value shaped the way its field declares it.

    A nested table becomes its dataclass, a section that is not in the
    document becomes a dataclass whose values are all absent, an array of
    tables becomes a tuple of them, a list becomes the tuple the field
    declares, and a text value of a path field becomes a Path. Anything else
    is handed over exactly as it was read.
    """

    if isinstance(field_type, type) and is_dataclass(field_type):
        return _build_section(field_type, raw)
    if get_origin(field_type) is tuple:
        if not isinstance(raw, list):
            return ()
        arguments = get_args(field_type)
        element_type = arguments[0] if arguments else None
        if isinstance(element_type, type) and is_dataclass(element_type):
            return tuple(
                _build_section(element_type, item)
                for item in raw
                if isinstance(item, dict)
            )
        return tuple(raw)
    if raw is None:
        return None
    if field_type is Path and isinstance(raw, str):
        return Path(raw)
    if (
        field_type is int
        and isinstance(raw, str)
        and len(raw) == 4
        and all(character in "01234567" for character in raw)
    ):
        # TOML has no octal literal, so a file mode is written as a string of
        # four octal digits and read as the int that chmod expects.
        return int(raw, 8)
    return raw


def _build_section(section_type: Any, raw: object) -> Any:
    """Build one section dataclass from the raw table with its name.

    Every field takes the value of the key with the same name. A table that
    is not there, or is not a table at all, leaves every field without a
    value.
    """

    table: dict[str, Any] = raw if isinstance(raw, dict) else {}
    hints = get_type_hints(section_type)
    return section_type(
        **{
            field.name: _build_value(hints.get(field.name), table.get(field.name))
            for field in fields(section_type)
        }
    )


def build_config_from_document(document: dict[str, Any]) -> Config:
    """Build the Config from an already parsed config document."""

    return _build_section(Config, document)  # type: ignore[no-any-return]


def load_config(path: Path) -> Config:
    """Read the config at path and return it.

    The read never fails: a path that does not exist, a file that cannot be
    read and a document that is not valid TOML all yield a Config whose
    values are absent.
    """

    try:
        document = tomllib.loads(render_config_source(path))
    except (OSError, tomllib.TOMLDecodeError):
        document = {}
    return build_config_from_document(document)
