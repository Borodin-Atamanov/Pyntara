# Configuration parser for the dnsproxy_setup section.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    ConfigError,
    _float_field,
    _int_field,
    _nonempty_string_field,
    _octal_mode_field,
)


@dataclass(frozen=True)
class DnsproxySetupConfig:
    # Configuration for the root-owned dnsproxy system service.

    github_repo: str
    download_dir: Path
    binary_path: Path
    service_unit_name: str
    service_unit_path: Path
    service_template_path: Path
    listen_addresses: tuple[str, ...]
    listen_port: int
    conflicting_package_name: str
    conflicting_service_units: tuple[str, ...]
    doh_url_format: str
    dot_host_format: str
    doq_host_format: str
    upstream_mode: str
    cache_enabled: bool
    fallback_resolvers: tuple[str, ...]
    bootstrap_resolvers: tuple[str, ...]
    query_log_path: Path
    query_log_mode: int
    service_restart_seconds: float
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    resolved_conf_dir: Path
    resolved_dropin_file_name: str
    resolved_dropin_file_mode: int
    resolved_dropin_header: str
    resolved_section: str
    resolved_dns_directives: tuple[str, ...]
    resolved_domains_directive: str
    manage_networkmanager: bool
    nmcli_check_command: tuple[str, ...]
    nmcli_list_command: tuple[str, ...]
    nmcli_modify_command: tuple[str, ...]
    daemon_reload_command: tuple[str, ...]
    restart_resolved_command: tuple[str, ...]
    resolvectl_status_command: tuple[str, ...]
    resolvectl_dns_command: tuple[str, ...]
    nmcli_dns_command: tuple[str, ...]
    verification_command: tuple[str, ...]
    profile_id_file_path: Path
    profile_id_file_mode: int


def _string_list(raw: dict[str, object], name: str) -> tuple[str, ...]:
    value = raw.get(name)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ConfigError(f"dnsproxy_setup.{name} must be a non-empty array of strings")
    return tuple(item.strip() for item in value)


def _dnsproxy_setup_table(raw: object) -> DnsproxySetupConfig:
    # Validate the dnsproxy_setup table.

    if not isinstance(raw, dict):
        raise ConfigError("[dnsproxy_setup] section is missing or not a table")
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "dnsproxy_setup.github_repo"
    )
    download_dir = Path(
        _nonempty_string_field(raw.get("download_dir"), "dnsproxy_setup.download_dir")
    )
    binary_path = Path(
        _nonempty_string_field(raw.get("binary_path"), "dnsproxy_setup.binary_path")
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "dnsproxy_setup.service_unit_name"
    )
    service_unit_path = Path(
        _nonempty_string_field(raw.get("service_unit_path"), "dnsproxy_setup.service_unit_path")
    )
    service_template_path = Path(
        _nonempty_string_field(
            raw.get("service_template_path"), "dnsproxy_setup.service_template_path"
        )
    )
    listen_addresses = _string_list(raw, "listen_addresses")
    conflicting_package_name = _nonempty_string_field(
        raw.get("conflicting_package_name"), "dnsproxy_setup.conflicting_package_name"
    )
    conflicting_service_units = _string_list(raw, "conflicting_service_units")
    listen_port = _int_field(raw.get("listen_port"), "dnsproxy_setup.listen_port")
    if not 1 <= listen_port <= 65535:
        raise ConfigError("dnsproxy_setup.listen_port must be between 1 and 65535")
    endpoint_values = tuple(
        _nonempty_string_field(raw.get(name), f"dnsproxy_setup.{name}")
        for name in ("doh_url_format", "dot_host_format", "doq_host_format")
    )
    if any("{profile_id}" not in value for value in endpoint_values):
        raise ConfigError("dnsproxy_setup endpoint formats must contain {profile_id}")
    upstream_mode = _nonempty_string_field(
        raw.get("upstream_mode"), "dnsproxy_setup.upstream_mode"
    )
    if upstream_mode not in ("load_balance", "parallel", "fastest_addr"):
        raise ConfigError("dnsproxy_setup.upstream_mode has an unsupported value")
    cache_enabled = raw.get("cache_enabled")
    if not isinstance(cache_enabled, bool):
        raise ConfigError("dnsproxy_setup.cache_enabled must be a boolean")
    fallback_resolvers = _string_list(raw, "fallback_resolvers")
    bootstrap_resolvers = _string_list(raw, "bootstrap_resolvers")
    query_log_path = Path(
        _nonempty_string_field(
            raw.get("query_log_path"), "dnsproxy_setup.query_log_path"
        )
    )
    query_log_mode = _octal_mode_field(
        raw.get("query_log_mode"), "dnsproxy_setup.query_log_mode"
    )
    service_restart_seconds = _float_field(
        raw.get("service_restart_seconds"), "dnsproxy_setup.service_restart_seconds"
    )
    if service_restart_seconds < 0:
        raise ConfigError("dnsproxy_setup.service_restart_seconds must not be negative")
    install_retries = _int_field(
        raw.get("install_retries"), "dnsproxy_setup.install_retries"
    )
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "dnsproxy_setup.start_check_attempts"
    )
    if install_retries < 1 or start_check_attempts < 1:
        raise ConfigError("dnsproxy_setup retry and readiness values must be positive")
    start_delay = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "dnsproxy_setup.start_check_retry_delay_seconds",
    )
    if start_delay <= 0:
        raise ConfigError(
            "dnsproxy_setup.start_check_retry_delay_seconds must be positive"
        )
    resolved_conf_dir = Path(
        _nonempty_string_field(
            raw.get("resolved_conf_dir"), "dnsproxy_setup.resolved_conf_dir"
        )
    )
    resolved_dropin_file_name = _nonempty_string_field(
        raw.get("resolved_dropin_file_name"), "dnsproxy_setup.resolved_dropin_file_name"
    )
    resolved_dropin_file_mode = _octal_mode_field(
        raw.get("resolved_dropin_file_mode"), "dnsproxy_setup.resolved_dropin_file_mode"
    )
    resolved_dropin_header = _nonempty_string_field(
        raw.get("resolved_dropin_header"), "dnsproxy_setup.resolved_dropin_header"
    )
    resolved_section = _nonempty_string_field(
        raw.get("resolved_section"), "dnsproxy_setup.resolved_section"
    )
    resolved_dns_directives = _string_list(raw, "resolved_dns_directives")
    resolved_domains_directive = _nonempty_string_field(
        raw.get("resolved_domains_directive"),
        "dnsproxy_setup.resolved_domains_directive",
    )
    manage_networkmanager = raw.get("manage_networkmanager")
    if not isinstance(manage_networkmanager, bool):
        raise ConfigError("dnsproxy_setup.manage_networkmanager must be a boolean")
    commands = tuple(
        _string_list(raw, name)
        for name in (
            "nmcli_check_command",
            "nmcli_list_command",
            "nmcli_modify_command",
            "daemon_reload_command",
            "restart_resolved_command",
            "resolvectl_status_command",
            "resolvectl_dns_command",
            "nmcli_dns_command",
            "verification_command",
        )
    )
    profile_id_file_path = Path(
        _nonempty_string_field(
            raw.get("profile_id_file_path"), "dnsproxy_setup.profile_id_file_path"
        )
    )
    profile_id_file_mode = _octal_mode_field(
        raw.get("profile_id_file_mode"), "dnsproxy_setup.profile_id_file_mode"
    )
    return DnsproxySetupConfig(
        github_repo=github_repo,
        download_dir=download_dir,
        binary_path=binary_path,
        service_unit_name=service_unit_name,
        service_unit_path=service_unit_path,
        service_template_path=service_template_path,
        listen_addresses=listen_addresses,
        listen_port=listen_port,
        conflicting_package_name=conflicting_package_name,
        conflicting_service_units=conflicting_service_units,
        doh_url_format=endpoint_values[0],
        dot_host_format=endpoint_values[1],
        doq_host_format=endpoint_values[2],
        upstream_mode=upstream_mode,
        cache_enabled=cache_enabled,
        fallback_resolvers=fallback_resolvers,
        bootstrap_resolvers=bootstrap_resolvers,
        query_log_path=query_log_path,
        query_log_mode=query_log_mode,
        service_restart_seconds=service_restart_seconds,
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_delay,
        resolved_conf_dir=resolved_conf_dir,
        resolved_dropin_file_name=resolved_dropin_file_name,
        resolved_dropin_file_mode=resolved_dropin_file_mode,
        resolved_dropin_header=resolved_dropin_header,
        resolved_section=resolved_section,
        resolved_dns_directives=resolved_dns_directives,
        resolved_domains_directive=resolved_domains_directive,
        manage_networkmanager=manage_networkmanager,
        nmcli_check_command=commands[0],
        nmcli_list_command=commands[1],
        nmcli_modify_command=commands[2],
        daemon_reload_command=commands[3],
        restart_resolved_command=commands[4],
        resolvectl_status_command=commands[5],
        resolvectl_dns_command=commands[6],
        nmcli_dns_command=commands[7],
        verification_command=commands[8],
        profile_id_file_path=profile_id_file_path,
        profile_id_file_mode=profile_id_file_mode,
    )
