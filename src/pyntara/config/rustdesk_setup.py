"""[rustdesk_setup] table: the RustDesk remote desktop client."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RustdeskOptionConfig:
    """One client option of the [rustdesk_setup.options] table.

    key is the rustdesk option name, value the value applied through
    rustdesk --option. The task reads the current value and sets the
    option only when it differs, so the options are idempotent.
    """

    key: str
    value: str


@dataclass(frozen=True)
class RustdeskSetupConfig:
    """RustDesk remote desktop client installed and configured.

    github_repo is the GitHub repository whose latest release provides the
    client deb; asset_name_template is the name of that deb with {version}
    and {asset_arch} substituted, the architecture part coming from the
    engine mapping release_asset_architectures; download_dir is where the
    deb is kept during the install;
    id_file_path and id_file_mode are the location and mode of the file
    that carries the machine RustDesk ID for the network report;
    vault_entry_title is the runtime vault entry that holds the permanent
    password and the machine RustDesk ID; service_unit_name is the
    rustdesk systemd unit;
    password_words and password_separator define the generated permanent
    password; config_dir is the rustdesk client configuration directory of
    the primary desktop user, whose identity file identity_file_name force
    mode removes to regenerate the machine ID. The client is driven through
    the configured commands: version_check_command, machine_id_command,
    get_option_command and set_option_command (each with the option key and
    value substituted) and set_password_command; service_stop_command,
    service_enable_command and service_start_command carry the service unit
    name, and readiness_probe_timeout_seconds bounds one machine ID probe
    inside the readiness loop. install_timeout_seconds,
    apt_update_timeout_seconds and install_retries bound the deb install;
    start_check_attempts and start_check_retry_delay_seconds are the
    readiness loop after the service start; options are the client
    options applied through rustdesk --option
    (docs/spec/rustdesk-setup.md).
    """

    github_repo: str
    asset_name_template: str
    version_check_command: tuple[str, ...]
    machine_id_command: tuple[str, ...]
    get_option_command: tuple[str, ...]
    set_option_command: tuple[str, ...]
    set_password_command: tuple[str, ...]
    service_stop_command: tuple[str, ...]
    service_enable_command: tuple[str, ...]
    service_start_command: tuple[str, ...]
    identity_file_name: str
    readiness_probe_timeout_seconds: int
    download_dir: Path
    id_file_path: Path
    id_file_mode: int
    vault_entry_title: str
    service_unit_name: str
    password_words: int
    password_separator: str
    config_dir: Path
    install_timeout_seconds: int
    apt_update_timeout_seconds: int
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    options: tuple[RustdeskOptionConfig, ...]
