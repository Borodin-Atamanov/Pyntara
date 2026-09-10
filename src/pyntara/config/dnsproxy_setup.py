from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    ss_tcp_listen_command: tuple[str, ...]
    ss_udp_listen_command: tuple[str, ...]
    kill_command: tuple[str, ...]
    service_log_command: tuple[str, ...]
    profile_id_file_path: Path
    profile_id_file_mode: int
