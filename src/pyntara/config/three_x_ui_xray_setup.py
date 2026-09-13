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
    REST API calls and the panel_ fields that follow it are the paths of
    that API, relative to the host, each with {placeholders} for the
    values of one call; vault_entry_title names the runtime vault entry
    where the credentials are stored. random_username_bytes,
    random_secret_bytes and random_sub_id_bytes are the lengths of the
    random part of every generated credential, which proquint encodes into
    the username and the client email, the password, the web base path and
    the client id, and the subscription id. server_ip_services lists the echo
    services queried for the public IPv4 address and
    server_ip_timeout_seconds bounds one such query.

    Stage 6 fields turn this machine into a client of the remote server
    through the same panel: client_profile_entry_title names the vault
    entry whose url is the canonical vless link of that server, and the
    local_proxy_ fields describe the inbound the panel serves for the
    machine itself (tag, listen address, port, UDP, sniffed protocols).

    Stage 7 fields are the routing policy of that proxy: the outbound
    tags the policy owns and the panel tags it jumps to, the local tor
    and i2p proxy addresses, the category lists (advertising, direct
    domains, direct address ranges, blocked in Russia, reachable only
    inside Russia, geo-restricted services), the two domain strategies,
    the country services with the word that names Russia, and the
    destinations the task asks the running core about afterwards.
    proxy_check_url is the URL queried once through the local proxy to
    prove the whole path, proxy_check_blocked_url is the URL of a class
    the policy sends through the remote server, queried on a machine in
    Russia, and proxy_check_timeout_seconds bounds one such request.
    route_test_port is the port the routing check knocks on for every
    destination class, and private_ipv4_networks are the CIDR networks
    that count as private when the task asks whether this machine sits
    behind NAT.

    The vocabulary of the panel itself is configured next to the values
    it is asked for: panel_inbound_protocol is the protocol of the local
    proxy inbound, panel_blocked_rule_protocols and
    panel_private_block_category are the words of the shipped rules the
    policy removes, panel_geodata_domain_kind and panel_geodata_ip_kind
    are the kinds the panel accepts in its geodata validation request, and
    inbound_sniffing_protocols are the protocols the panel is asked to
    detect on the universal inbound.
    """

    github_repo: str
    install_script_url: str
    install_dir: Path
    binary_file_name: str
    service_process_name: str
    panel_version_command: tuple[str, ...]
    panel_settings_query_command: tuple[str, ...]
    panel_cert_query_command: tuple[str, ...]
    panel_port_command: tuple[str, ...]
    panel_credentials_command: tuple[str, ...]
    panel_certificate_command: tuple[str, ...]
    installer_run_command: tuple[str, ...]
    acme_install_command: tuple[str, ...]
    acme_dir_relative_path: str
    acme_file_name: str
    acme_port_listener_command: tuple[str, ...]
    acme_set_default_ca_command: tuple[str, ...]
    acme_issue_command: tuple[str, ...]
    acme_installcert_command: tuple[str, ...]
    acme_upgrade_command: tuple[str, ...]
    acme_reload_command: str
    openssl_check_command: tuple[str, ...]
    openssl_generate_command: tuple[str, ...]
    openssl_subject_template: str
    service_restart_command: tuple[str, ...]
    service_unit_name: str
    start_check_attempts: int
    start_check_retry_delay_seconds: int
    install_result_env_path: Path
    inbound_payload_template_file_name: str
    random_username_bytes: int
    random_secret_bytes: int
    random_sub_id_bytes: int
    panel_port: int
    ssl_enabled: bool
    panel_http_address: str
    panel_root_path: str
    panel_login_path: str
    panel_csrf_token_path: str
    panel_inbounds_list_path: str
    panel_inbounds_add_path: str
    panel_inbounds_update_path: str
    panel_inbounds_delete_path: str
    panel_client_get_path: str
    panel_client_add_path: str
    panel_client_links_path: str
    panel_x25519_cert_path: str
    panel_setting_all_path: str
    panel_setting_update_path: str
    panel_xray_status_path: str
    panel_xray_update_path: str
    panel_xray_geodata_validate_path: str
    panel_xray_route_test_path: str
    panel_inbound_protocol: str
    panel_blocked_rule_protocols: tuple[str, ...]
    panel_private_block_category: str
    panel_geodata_domain_kind: str
    panel_geodata_ip_kind: str
    inbound_sniffing_protocols: tuple[str, ...]
    vault_entry_title: str
    connection_vault_entry_title: str
    share_addr_strategy: str
    inbound_port: int
    route_test_port: int
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
    cert_privkey_file_mode: int
    cert_fullchain_file_mode: int
    self_signed_cert_dir: Path
    self_signed_cert_fullchain: Path
    self_signed_cert_privkey: Path
    server_ip_timeout_seconds: int
    server_ip_services: tuple[str, ...]
    probe_timeout_seconds: int
    probe_port_80_timeout_seconds: int
    probe_listener_start_seconds: int
    port_forward_probe_command: tuple[str, ...]
    port_forward_probe_url_format: str
    panel_probe_command: tuple[str, ...]
    tunnel_probe_command: tuple[str, ...]
    tunnel_probe_write_out: str
    upnp_enabled: bool
    upnp_package: str
    upnp_client_command: str
    upnp_protocol: str
    upnp_mapping_description: str
    client_profile_entry_title: str
    local_proxy_tag: str
    local_proxy_listen_address: str
    private_ipv4_networks: tuple[str, ...]
    local_proxy_port: int
    local_proxy_udp: bool
    local_proxy_sniffing_protocols: tuple[str, ...]
    remote_outbound_tag: str
    tor_outbound_tag: str
    i2p_outbound_tag: str
    direct_outbound_tag: str
    blocked_outbound_tag: str
    tor_proxy_address: str
    i2p_proxy_address: str
    ad_block_domain_categories: tuple[str, ...]
    direct_domains: tuple[str, ...]
    direct_ip_categories: tuple[str, ...]
    direct_ip_networks: tuple[str, ...]
    country_services: tuple[str, ...]
    country_word: str
    country_query_timeout_seconds: int
    country_command_timeout_seconds: int
    russia_blocked_domain_categories: tuple[str, ...]
    russia_blocked_ip_categories: tuple[str, ...]
    russia_direct_domain_categories: tuple[str, ...]
    russia_direct_ip_categories: tuple[str, ...]
    geo_restricted_domain_categories: tuple[str, ...]
    russia_domain_strategy: str
    outside_russia_domain_strategy: str
    route_check_ad_domain: str
    route_check_foreign_domain: str
    route_check_onion_domain: str
    route_check_i2p_domain: str
    route_check_direct_domain: str
    route_check_russia_blocked_domain: str
    proxy_check_url: str
    proxy_check_blocked_url: str
    proxy_check_timeout_seconds: int
    proxy_check_command_timeout_seconds: int
