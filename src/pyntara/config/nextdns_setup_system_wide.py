"""[nextdns_setup_system_wide] table parser.

The section carries the parameters of the NextDNS profile selection
task: the vault group that holds the profile accounts, the path and mode
of the file that records the selected profile ID and the syslog priority
of a serious failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    ConfigError,
    _int_field,
    _nonempty_string_field,
    _octal_mode_field,
)


@dataclass(frozen=True)
class NextdnsSetupSystemWideConfig:
    """NextDNS profile selection parameters for the nextdns_setup_system_wide task.

    vault_group_title names the vault subgroup that carries the NextDNS
    profile accounts (the [vault_structure] groups). profile_id_file_path
    and profile_id_file_mode are the path and mode of the file that
    records the selected profile ID for dnsproxy_setup and the System
    Metrics collector. error_priority is the syslog priority of a serious
    failure.
    """

    vault_group_title: str
    profile_id_file_path: Path
    profile_id_file_mode: int
    error_priority: int


def _nextdns_setup_system_wide_table(
    raw: object,
) -> NextdnsSetupSystemWideConfig:
    """Validate the [nextdns_setup_system_wide] table and build the config.

    Every value is required and typed: the vault group title and the
    profile ID file path are non-empty strings; profile_id_file_mode is
    an octal string and error_priority is an integer 0-7.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[nextdns_setup_system_wide] section is missing or not a table"
        )
    vault_group_title = _nonempty_string_field(
        raw.get("vault_group_title"), "nextdns_setup_system_wide.vault_group_title"
    )
    profile_id_file_path = _nonempty_string_field(
        raw.get("profile_id_file_path"),
        "nextdns_setup_system_wide.profile_id_file_path",
    )
    profile_id_file_mode = _octal_mode_field(
        raw.get("profile_id_file_mode"),
        "nextdns_setup_system_wide.profile_id_file_mode",
    )
    error_priority = _int_field(
        raw.get("error_priority"), "nextdns_setup_system_wide.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "nextdns_setup_system_wide.error_priority must be between 0 and 7"
        )
    return NextdnsSetupSystemWideConfig(
        vault_group_title=vault_group_title,
        profile_id_file_path=Path(profile_id_file_path),
        profile_id_file_mode=profile_id_file_mode,
        error_priority=error_priority,
    )
