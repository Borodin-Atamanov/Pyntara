"""[three_x_ui_xray_setup] table: 3x-ui installation parameters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _int_field, _nonempty_string_field


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
    for the service to become active after an install. Stage 2 fields:
    install_result_env_path is the file the panel writes on first start
    with the generated credentials; panel_http_address is the host for
    REST API calls; vault_entry_title names the runtime vault entry
    where the credentials are stored.
    """

    github_repo: str
    install_script_url: str
    install_dir: Path
    service_unit_name: str
    start_check_attempts: int
    start_check_retry_delay_seconds: int
    install_result_env_path: Path
    panel_http_address: str
    vault_entry_title: str


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
    panel_http_address = _nonempty_string_field(
        raw.get("panel_http_address"),
        "three_x_ui_xray_setup.panel_http_address",
    )
    vault_entry_title = _nonempty_string_field(
        raw.get("vault_entry_title"),
        "three_x_ui_xray_setup.vault_entry_title",
    )
    return ThreeXuiXraySetupConfig(
        github_repo=github_repo,
        install_script_url=install_script_url,
        install_dir=install_dir,
        service_unit_name=service_unit_name,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        install_result_env_path=install_result_env_path,
        panel_http_address=panel_http_address,
        vault_entry_title=vault_entry_title,
    )
