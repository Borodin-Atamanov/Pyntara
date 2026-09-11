from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DnsproxySetupConfig:
    # Configuration for the root-owned dnsproxy system service.
    # verification_error_excerpt_length and service_log_excerpt_length
    # carry the lengths of the diagnostic texts the task reports.
    # asset_name_template names the release asset with {asset_arch} and
    # {release_tag} (the tag keeps its leading v, as the published name
    # carries it), asset_architecture_names maps the dpkg architecture to the
    # spelling dnsproxy uses in that name (its own table, not the engine
    # one, because dnsproxy names its architectures differently),
    # binary_file_name is the binary inside the archive,
    # staged_binary_file_name the copy next to it that is probed for the
    # version and extract_dir_name the directory the archive is unpacked
    # into. probe_address and probe_ident_bytes are the address and the
    # identifier size of the direct DNS probe; tun_device_type and
    # loopback_connection_name are the NetworkManager tokens the auto DNS
    # sweep skips; nmcli_auto_dns_ignored_value is the state query answer
    # that means auto DNS is already ignored, and the two following values
    # are what the sweep writes to ignore and to restore it.
    # service_stop_command, service_enable_command, service_start_command
    # and service_restart_command carry {service_unit_name}.

    github_repo: str
    asset_name_template: str
    asset_architecture_names: dict[str, str]
    binary_file_name: str
    staged_binary_file_name: str
    extract_dir_name: str
    download_dir: Path
    binary_path: Path
    service_unit_name: str
    service_unit_path: Path
    service_template_path: Path
    listen_addresses: tuple[str, ...]
    listen_port: int
    doh_url_format: str
    dot_host_format: str
    doq_host_format: str
    upstream_mode: str
    cache_enabled: bool
    cache_size_bytes: int
    timeout_seconds: int
    log_rate_limit_interval_seconds: int
    log_rate_limit_burst: int
    bootstrap_resolvers: tuple[str, ...]
    append_provider_dns: bool
    service_restart_seconds: float
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    resolved_conf_dir: Path
    resolved_dropin_file_name: str
    resolved_dropin_file_mode: int
    staged_binary_file_mode: int
    resolved_dropin_header: str
    resolved_section: str
    resolved_dns_directives: tuple[str, ...]
    resolved_domains_directive: str
    manage_networkmanager: bool
    nmcli_check_command: tuple[str, ...]
    nmcli_device_status_command: tuple[str, ...]
    nmcli_active_list_command: tuple[str, ...]
    nmcli_dns_state_command: tuple[str, ...]
    nmcli_modify_command: tuple[str, ...]
    nmcli_reapply_command: tuple[str, ...]
    daemon_reload_command: tuple[str, ...]
    restart_resolved_command: tuple[str, ...]
    resolvectl_status_command: tuple[str, ...]
    resolvectl_dns_command: tuple[str, ...]
    nmcli_dns_command: tuple[str, ...]
    verification_command: tuple[str, ...]
    verification_domain: str
    probe_address: str
    probe_ident_bytes: int
    tun_device_type: str
    loopback_connection_name: str
    nmcli_auto_dns_ignored_value: str
    nmcli_ignore_auto_dns_value: str
    nmcli_restore_auto_dns_value: str
    service_stop_command: tuple[str, ...]
    service_enable_command: tuple[str, ...]
    service_start_command: tuple[str, ...]
    service_restart_command: tuple[str, ...]
    verification_error_excerpt_length: int
    ss_tcp_listen_command: tuple[str, ...]
    ss_udp_listen_command: tuple[str, ...]
    kill_command: tuple[str, ...]
    service_log_command: tuple[str, ...]
    service_log_excerpt_length: int
    profile_id_file_path: Path
    profile_id_file_mode: int
