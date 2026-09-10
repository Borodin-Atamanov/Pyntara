"""[three_x_ui_xray_setup] table: 3x-ui installation parameters."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ThreeXuiXraySetupConfig:
    """3x-ui installation parameters for the three_x_ui_xray_setup task.

    The task wraps the official 3x-ui installer: github_repo is the
    owner/name of the repository whose latest release tag is compared
    with the installed version; install_script_url is where the official
    install.sh is downloaded from; install_dir is the directory holding
    the x-ui binary whose -v output gives the installed version;
    service_unit_name is the systemd unit the official installer creates
    and the task checks for enabled and active; start_check_attempts and
    start_check_retry_delay_seconds form the readiness loop that waits
    for the service to become active after an install. panel_port is the
    fixed panel port passed to the installer via XUI_PANEL_PORT; the
    installer applies it on first deployment and preserves the current
    port on an existing panel with custom credentials. ssl_enabled turns
    on the Let's Encrypt IP certificate setup (installer option 2): the
    installer runs with XUI_SSL_MODE=ip, the ACME port is freed before
    SSL setup, and on a rerun the task issues the certificate through
    acme.sh when the panel has none. Stage 2 fields:
    install_result_env_path is the file the panel writes on first start
    with the generated credentials; panel_http_address is the host for
    REST API calls; vault_entry_title names the runtime vault entry
    where the credentials are stored. server_ip_services lists the echo
    services queried for the public IPv4 address and
    server_ip_timeout_seconds bounds one such query.
    """

    github_repo: str
    install_script_url: str
    install_dir: Path
    service_unit_name: str
    start_check_attempts: int
    start_check_retry_delay_seconds: int
    install_result_env_path: Path
    panel_port: int
    ssl_enabled: bool
    panel_http_address: str
    vault_entry_title: str
    connection_vault_entry_title: str
    share_addr_strategy: str
    inbound_port: int
    inbound_remark: str
    reality_dest: str
    reality_server_names: tuple[str, ...]
    reality_short_id: str
    reality_fingerprint: str
    subscription_path: str
    subscription_json_path: str
    subscription_clash_path: str
    acme_port: int
    cert_dir: Path
    cert_fullchain: Path
    cert_privkey: Path
    self_signed_cert_dir: Path
    self_signed_cert_fullchain: Path
    self_signed_cert_privkey: Path
    server_ip_timeout_seconds: int
    server_ip_services: tuple[str, ...]
    probe_timeout_seconds: int
    probe_port_80_timeout_seconds: int
    probe_listener_start_seconds: int
    upnp_enabled: bool
    upnp_package: str
    upnp_client_command: str
    upnp_mapping_description: str
