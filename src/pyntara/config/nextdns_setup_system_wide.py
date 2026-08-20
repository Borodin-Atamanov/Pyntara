"""[nextdns_setup_system_wide] table parser.

The section carries the parameters of the system-wide NextDNS task: where
the vault group lives, where the resolved.conf drop-in is written and how
the resolver behaves. The vocabulary of dns_over_tls is validated against
the DNS_OVER_TLS_VALUES constant of _fields.py, like every other config
vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    DNS_OVER_TLS_VALUES,
    ConfigError,
    _int_field,
    _nonempty_string_field,
    _octal_mode_field,
)


@dataclass(frozen=True)
class NextdnsSetupSystemWideConfig:
    """System-wide NextDNS parameters for the nextdns_setup_system_wide task.

    vault_group_title names the vault subgroup that carries the NextDNS
    profile accounts (the [vault_structure] groups). The task writes a
    drop-in into resolved_conf_dir with the name dropin_file_name and the
    mode dropin_file_mode. dns_over_tls is the DNSOverTLS mode of
    systemd-resolved, fallback_dns the servers that answer when NextDNS is
    unreachable. manage_networkmanager tells the task to clear per-link
    DNS in NetworkManager so the global NextDNS servers are actually used.
    error_priority is the syslog priority of a serious failure,
    command_timeout_seconds the ceiling of a verification command.
    """

    vault_group_title: str
    resolved_conf_dir: Path
    dropin_file_name: str
    dropin_file_mode: int
    dns_over_tls: str
    fallback_dns: tuple[str, ...]
    manage_networkmanager: bool
    error_priority: int
    command_timeout_seconds: int


def _nextdns_setup_system_wide_table(
    raw: object,
) -> NextdnsSetupSystemWideConfig:
    """Validate the [nextdns_setup_system_wide] table and build the config.

    Every value is required and typed: the vault group title, the drop-in
    directory and file name are non-empty strings, the drop-in mode is an
    octal string, dns_over_tls is one of the DNSOverTLS vocabulary,
    fallback_dns is a non-empty array of non-empty strings,
    manage_networkmanager is a boolean, the priority and timeout are
    integers within their ranges.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[nextdns_setup_system_wide] section is missing or not a table"
        )
    vault_group_title = _nonempty_string_field(
        raw.get("vault_group_title"), "nextdns_setup_system_wide.vault_group_title"
    )
    resolved_conf_dir = _nonempty_string_field(
        raw.get("resolved_conf_dir"), "nextdns_setup_system_wide.resolved_conf_dir"
    )
    dropin_file_name = _nonempty_string_field(
        raw.get("dropin_file_name"), "nextdns_setup_system_wide.dropin_file_name"
    )
    dropin_file_mode = _octal_mode_field(
        raw.get("dropin_file_mode"), "nextdns_setup_system_wide.dropin_file_mode"
    )
    dns_over_tls = _nonempty_string_field(
        raw.get("dns_over_tls"), "nextdns_setup_system_wide.dns_over_tls"
    )
    if dns_over_tls not in DNS_OVER_TLS_VALUES:
        raise ConfigError(
            f"nextdns_setup_system_wide.dns_over_tls must be one of "
            f"{', '.join(DNS_OVER_TLS_VALUES)}"
        )
    fallback_dns_raw = raw.get("fallback_dns")
    if not isinstance(fallback_dns_raw, list) or not fallback_dns_raw:
        raise ConfigError(
            "nextdns_setup_system_wide.fallback_dns must be a non-empty "
            "array of strings"
        )
    fallback_dns: list[str] = []
    for server in fallback_dns_raw:
        if not isinstance(server, str) or not server.strip():
            raise ConfigError(
                "nextdns_setup_system_wide.fallback_dns must be non-empty strings"
            )
        fallback_dns.append(server.strip())
    manage_networkmanager = raw.get("manage_networkmanager")
    if not isinstance(manage_networkmanager, bool):
        raise ConfigError(
            "nextdns_setup_system_wide.manage_networkmanager must be a boolean"
        )
    error_priority = _int_field(
        raw.get("error_priority"), "nextdns_setup_system_wide.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "nextdns_setup_system_wide.error_priority must be between 0 and 7"
        )
    command_timeout_seconds = _int_field(
        raw.get("command_timeout_seconds"),
        "nextdns_setup_system_wide.command_timeout_seconds",
    )
    if command_timeout_seconds <= 0:
        raise ConfigError(
            "nextdns_setup_system_wide.command_timeout_seconds must be positive"
        )
    return NextdnsSetupSystemWideConfig(
        vault_group_title=vault_group_title,
        resolved_conf_dir=Path(resolved_conf_dir),
        dropin_file_name=dropin_file_name,
        dropin_file_mode=dropin_file_mode,
        dns_over_tls=dns_over_tls,
        fallback_dns=tuple(fallback_dns),
        manage_networkmanager=manage_networkmanager,
        error_priority=error_priority,
        command_timeout_seconds=command_timeout_seconds,
    )
