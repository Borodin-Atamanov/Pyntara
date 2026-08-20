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
    mode dropin_file_mode; resolve_section and dropin_header are the
    section header and the ownership comment of the drop-in, and
    domains_directive the Domains value that routes every query through
    the global resolver. ipv4_servers, ipv6_prefixes, dot_endpoint_format
    and verification_url are the NextDNS service contract: the anycast
    addresses, the DoT endpoint pattern with the {profile_id} placeholder
    and the verification endpoint. dns_over_tls is the DNSOverTLS mode of
    systemd-resolved, fallback_dns the servers that answer when NextDNS
    is unreachable. manage_networkmanager tells the task to clear per-link
    DNS in NetworkManager so the global NextDNS servers are actually
    used. directive_keys are the drop-in directive keys the task owns;
    nmcli_check_command, nmcli_list_command and nmcli_modify_command the
    NetworkManager commands (the modify command carries the {connection}
    and {value} placeholders), restart_resolved_command the resolver
    restart, resolvectl_status_command the state query and
    verification_command the endpoint query (with the {url} and
    {timeout} placeholders). error_priority is the syslog priority of a
    serious failure, command_timeout_seconds the ceiling of a
    verification command.
    """

    vault_group_title: str
    resolved_conf_dir: Path
    dropin_file_name: str
    dropin_file_mode: int
    resolve_section: str
    dropin_header: str
    domains_directive: str
    ipv4_servers: tuple[str, ...]
    ipv6_prefixes: tuple[str, ...]
    dot_endpoint_format: str
    verification_url: str
    dns_over_tls: str
    fallback_dns: tuple[str, ...]
    directive_keys: tuple[str, ...]
    nmcli_check_command: tuple[str, ...]
    nmcli_list_command: tuple[str, ...]
    nmcli_modify_command: tuple[str, ...]
    restart_resolved_command: tuple[str, ...]
    resolvectl_status_command: tuple[str, ...]
    verification_command: tuple[str, ...]
    manage_networkmanager: bool
    error_priority: int
    command_timeout_seconds: int


def _nextdns_setup_system_wide_table(
    raw: object,
) -> NextdnsSetupSystemWideConfig:
    """Validate the [nextdns_setup_system_wide] table and build the config.

    Every value is required and typed: the vault group title, the drop-in
    directory, file name and the section/header strings are non-empty,
    the drop-in mode is an octal string, ipv4_servers, ipv6_prefixes,
    fallback_dns, directive_keys and every command array are non-empty
    arrays of non-empty strings, dot_endpoint_format must carry the
    {profile_id} placeholder, nmcli_modify_command the {connection} and
    {value} placeholders, verification_command the {url} and {timeout}
    placeholders, dns_over_tls is one of the DNSOverTLS vocabulary,
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
    resolve_section = _nonempty_string_field(
        raw.get("resolve_section"), "nextdns_setup_system_wide.resolve_section"
    )
    dropin_header = _nonempty_string_field(
        raw.get("dropin_header"), "nextdns_setup_system_wide.dropin_header"
    )
    domains_directive = _nonempty_string_field(
        raw.get("domains_directive"), "nextdns_setup_system_wide.domains_directive"
    )

    def _string_list_field(name: str) -> tuple[str, ...]:
        """Validate a non-empty array of non-empty strings."""

        raw_value = raw.get(name)
        if not isinstance(raw_value, list) or not raw_value:
            raise ConfigError(
                f"nextdns_setup_system_wide.{name} must be a non-empty "
                "array of strings"
            )
        values: list[str] = []
        for server in raw_value:
            if not isinstance(server, str) or not server.strip():
                raise ConfigError(
                    f"nextdns_setup_system_wide.{name} must be non-empty strings"
                )
            values.append(server.strip())
        return tuple(values)

    ipv4_servers = _string_list_field("ipv4_servers")
    ipv6_prefixes = _string_list_field("ipv6_prefixes")
    dot_endpoint_format = _nonempty_string_field(
        raw.get("dot_endpoint_format"),
        "nextdns_setup_system_wide.dot_endpoint_format",
    )
    if "{profile_id}" not in dot_endpoint_format:
        raise ConfigError(
            "nextdns_setup_system_wide.dot_endpoint_format must contain "
            "the {profile_id} placeholder"
        )
    verification_url = _nonempty_string_field(
        raw.get("verification_url"), "nextdns_setup_system_wide.verification_url"
    )
    dns_over_tls = _nonempty_string_field(
        raw.get("dns_over_tls"), "nextdns_setup_system_wide.dns_over_tls"
    )
    if dns_over_tls not in DNS_OVER_TLS_VALUES:
        raise ConfigError(
            f"nextdns_setup_system_wide.dns_over_tls must be one of "
            f"{', '.join(DNS_OVER_TLS_VALUES)}"
        )
    fallback_dns = _string_list_field("fallback_dns")
    directive_keys = _string_list_field("directive_keys")
    nmcli_check_command = _string_list_field("nmcli_check_command")
    nmcli_list_command = _string_list_field("nmcli_list_command")
    nmcli_modify_command = _string_list_field("nmcli_modify_command")
    if "{connection}" not in nmcli_modify_command or "{value}" not in nmcli_modify_command:
        raise ConfigError(
            "nextdns_setup_system_wide.nmcli_modify_command must contain "
            "the {connection} and {value} placeholders"
        )
    restart_resolved_command = _string_list_field("restart_resolved_command")
    resolvectl_status_command = _string_list_field("resolvectl_status_command")
    verification_command = _string_list_field("verification_command")
    if "{url}" not in verification_command or "{timeout}" not in verification_command:
        raise ConfigError(
            "nextdns_setup_system_wide.verification_command must contain "
            "the {url} and {timeout} placeholders"
        )
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
        resolve_section=resolve_section,
        dropin_header=dropin_header,
        domains_directive=domains_directive,
        ipv4_servers=ipv4_servers,
        ipv6_prefixes=ipv6_prefixes,
        dot_endpoint_format=dot_endpoint_format,
        verification_url=verification_url,
        dns_over_tls=dns_over_tls,
        fallback_dns=fallback_dns,
        directive_keys=directive_keys,
        nmcli_check_command=nmcli_check_command,
        nmcli_list_command=nmcli_list_command,
        nmcli_modify_command=nmcli_modify_command,
        restart_resolved_command=restart_resolved_command,
        resolvectl_status_command=resolvectl_status_command,
        verification_command=verification_command,
        manage_networkmanager=manage_networkmanager,
        error_priority=error_priority,
        command_timeout_seconds=command_timeout_seconds,
    )
