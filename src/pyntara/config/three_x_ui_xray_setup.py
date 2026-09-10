"""[three_x_ui_xray_setup] table: 3x-ui installation parameters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    SHARE_ADDR_STRATEGIES,
    ConfigError,
    _bool_field,
    _int_field,
    _nonempty_string_field,
    _string_list,
)


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


def _three_x_ui_xray_setup_table(raw: object) -> ThreeXuiXraySetupConfig:
    """Validate the [three_x_ui_xray_setup] table and build the config.

    github_repo, install_script_url, install_dir, service_unit_name,
    install_result_env_path, panel_http_address and vault_entry_title are
    non-empty strings; start_check_attempts is positive and
    start_check_retry_delay_seconds is non-negative.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[three_x_ui_xray_setup] section is missing or not a table"
        )
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "three_x_ui_xray_setup.github_repo"
    )
    install_script_url = _nonempty_string_field(
        raw.get("install_script_url"),
        "three_x_ui_xray_setup.install_script_url",
    )
    install_dir = Path(
        _nonempty_string_field(
            raw.get("install_dir"), "three_x_ui_xray_setup.install_dir"
        )
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"),
        "three_x_ui_xray_setup.service_unit_name",
    )
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"),
        "three_x_ui_xray_setup.start_check_attempts",
    )
    if start_check_attempts < 1:
        raise ConfigError(
            "three_x_ui_xray_setup.start_check_attempts must be positive"
        )
    start_check_retry_delay_seconds = _int_field(
        raw.get("start_check_retry_delay_seconds"),
        "three_x_ui_xray_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds < 0:
        raise ConfigError(
            "three_x_ui_xray_setup.start_check_retry_delay_seconds "
            "must not be negative"
        )
    install_result_env_path = Path(
        _nonempty_string_field(
            raw.get("install_result_env_path"),
            "three_x_ui_xray_setup.install_result_env_path",
        )
    )
    panel_port = _int_field(
        raw.get("panel_port"),
        "three_x_ui_xray_setup.panel_port",
    )
    if panel_port < 1 or panel_port > 65535:
        raise ConfigError(
            "three_x_ui_xray_setup.panel_port must be between 1 and 65535"
        )
    ssl_enabled = _bool_field(
        raw.get("ssl_enabled"),
        "three_x_ui_xray_setup.ssl_enabled",
    )
    panel_http_address = _nonempty_string_field(
        raw.get("panel_http_address"),
        "three_x_ui_xray_setup.panel_http_address",
    )
    vault_entry_title = _nonempty_string_field(
        raw.get("vault_entry_title"),
        "three_x_ui_xray_setup.vault_entry_title",
    )
    connection_vault_entry_title = _nonempty_string_field(
        raw.get("connection_vault_entry_title"),
        "three_x_ui_xray_setup.connection_vault_entry_title",
    )
    share_addr_strategy = _nonempty_string_field(
        raw.get("share_addr_strategy"),
        "three_x_ui_xray_setup.share_addr_strategy",
    )
    if share_addr_strategy not in SHARE_ADDR_STRATEGIES:
        raise ConfigError(
            "three_x_ui_xray_setup.share_addr_strategy must be one of "
            + ", ".join(SHARE_ADDR_STRATEGIES)
        )
    inbound_port = _int_field(
        raw.get("inbound_port"),
        "three_x_ui_xray_setup.inbound_port",
    )
    if inbound_port < 1 or inbound_port > 65535:
        raise ConfigError(
            "three_x_ui_xray_setup.inbound_port must be between 1 and 65535"
        )
    inbound_remark = _nonempty_string_field(
        raw.get("inbound_remark"),
        "three_x_ui_xray_setup.inbound_remark",
    )
    reality_dest = _nonempty_string_field(
        raw.get("reality_dest"),
        "three_x_ui_xray_setup.reality_dest",
    )
    reality_server_names = _string_list(
        raw.get("reality_server_names"),
        "three_x_ui_xray_setup.reality_server_names",
    )
    reality_short_id = _nonempty_string_field(
        raw.get("reality_short_id"),
        "three_x_ui_xray_setup.reality_short_id",
    )
    reality_fingerprint = _nonempty_string_field(
        raw.get("reality_fingerprint"),
        "three_x_ui_xray_setup.reality_fingerprint",
    )
    subscription_path = _subscription_path_field(
        raw.get("subscription_path"),
        "three_x_ui_xray_setup.subscription_path",
    )
    subscription_json_path = _subscription_path_field(
        raw.get("subscription_json_path"),
        "three_x_ui_xray_setup.subscription_json_path",
    )
    subscription_clash_path = _subscription_path_field(
        raw.get("subscription_clash_path"),
        "three_x_ui_xray_setup.subscription_clash_path",
    )
    acme_port = _int_field(
        raw.get("acme_port"),
        "three_x_ui_xray_setup.acme_port",
    )
    if acme_port < 1 or acme_port > 65535:
        raise ConfigError(
            "three_x_ui_xray_setup.acme_port must be between 1 and 65535"
        )
    cert_dir = Path(
        _nonempty_string_field(
            raw.get("cert_dir"), "three_x_ui_xray_setup.cert_dir"
        )
    )
    self_signed_cert_dir = Path(
        _nonempty_string_field(
            raw.get("self_signed_cert_dir"),
            "three_x_ui_xray_setup.self_signed_cert_dir",
        )
    )
    server_ip_timeout_seconds = _int_field(
        raw.get("server_ip_timeout_seconds"),
        "three_x_ui_xray_setup.server_ip_timeout_seconds",
    )
    if server_ip_timeout_seconds < 1:
        raise ConfigError(
            "three_x_ui_xray_setup.server_ip_timeout_seconds must be positive"
        )
    server_ip_services = _string_list(
        raw.get("server_ip_services"),
        "three_x_ui_xray_setup.server_ip_services",
    )
    return ThreeXuiXraySetupConfig(
        github_repo=github_repo,
        install_script_url=install_script_url,
        install_dir=install_dir,
        service_unit_name=service_unit_name,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        install_result_env_path=install_result_env_path,
        panel_port=panel_port,
        ssl_enabled=ssl_enabled,
        panel_http_address=panel_http_address,
        vault_entry_title=vault_entry_title,
        connection_vault_entry_title=connection_vault_entry_title,
        share_addr_strategy=share_addr_strategy,
        inbound_port=inbound_port,
        inbound_remark=inbound_remark,
        reality_dest=reality_dest,
        reality_server_names=reality_server_names,
        reality_short_id=reality_short_id,
        reality_fingerprint=reality_fingerprint,
        subscription_path=subscription_path,
        subscription_json_path=subscription_json_path,
        subscription_clash_path=subscription_clash_path,
        acme_port=acme_port,
        cert_dir=cert_dir,
        cert_fullchain=cert_dir / "fullchain.pem",
        cert_privkey=cert_dir / "privkey.pem",
        self_signed_cert_dir=self_signed_cert_dir,
        self_signed_cert_fullchain=self_signed_cert_dir / "fullchain.pem",
        self_signed_cert_privkey=self_signed_cert_dir / "privkey.pem",
        server_ip_timeout_seconds=server_ip_timeout_seconds,
        server_ip_services=server_ip_services,
    )


def _subscription_path_field(value: object, name: str) -> str:
    """A panel subscription path: a non-empty string wrapped in slashes.

    The panel warns about its well-known defaults (/sub/, /json/,
    /clash/), so the task writes its own paths. A value without the
    leading and trailing slash would be normalized differently by the
    panel and is rejected here.
    """

    text = _nonempty_string_field(value, name)
    if len(text) < 3 or not text.startswith("/") or not text.endswith("/"):
        raise ConfigError(f"{name} must start and end with a slash")
    return text
