"""[rustdesk_setup] table: the RustDesk remote desktop client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _float_field, _int_field, _octal_mode_field


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
    client deb; download_dir is where the deb is kept during the install;
    id_file_path and id_file_mode are the location and mode of the file
    that carries the machine RustDesk ID for the network report;
    vault_entry_title is the runtime vault entry that holds the permanent
    password; service_unit_name is the rustdesk systemd unit;
    password_words and password_separator define the generated permanent
    password; config_dir is the rustdesk client configuration directory of
    the primary desktop user, whose identity file force mode removes to
    regenerate the machine ID; install_timeout_seconds,
    apt_update_timeout_seconds and install_retries bound the deb install;
    api_timeout_seconds bounds the GitHub releases API call;
    start_check_attempts and start_check_retry_delay_seconds are the
    readiness loop after the service start; options are the client
    options applied through rustdesk --option
    (docs/spec/rustdesk-setup.md).
    """

    github_repo: str
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
    api_timeout_seconds: float
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    options: tuple[RustdeskOptionConfig, ...]


def _rustdesk_options(raw: object) -> tuple[RustdeskOptionConfig, ...]:
    """Validate the [rustdesk_setup.options] array of tables.

    Every option is a table with a non-empty key and a non-empty value; a
    missing array means no options. Duplicate keys are a config error, so
    the task never applies the same option twice with different values.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("[rustdesk_setup] options must be an array of tables")
    result: list[RustdeskOptionConfig] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError("[rustdesk_setup] option entries must be tables")
        key = entry.get("key")
        value = entry.get("value")
        if not isinstance(key, str) or not key:
            raise ConfigError("[rustdesk_setup] option key must be a non-empty string")
        if not isinstance(value, str) or not value:
            raise ConfigError("[rustdesk_setup] option value must be a non-empty string")
        if key in seen:
            raise ConfigError(f"[rustdesk_setup] duplicate option key: {key}")
        seen.add(key)
        result.append(RustdeskOptionConfig(key=key, value=value))
    return tuple(result)


def _rustdesk_setup_table(raw: object) -> RustdeskSetupConfig:
    """Validate the [rustdesk_setup] table and build RustdeskSetupConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[rustdesk_setup] section is missing or not a table")
    github_repo = raw.get("github_repo")
    if not isinstance(github_repo, str) or not github_repo:
        raise ConfigError("rustdesk_setup.github_repo must be a non-empty string")
    download_dir = raw.get("download_dir")
    if not isinstance(download_dir, str):
        raise ConfigError("rustdesk_setup.download_dir must be a string")
    id_file_path = raw.get("id_file_path")
    if not isinstance(id_file_path, str):
        raise ConfigError("rustdesk_setup.id_file_path must be a string")
    vault_entry_title = raw.get("vault_entry_title")
    if not isinstance(vault_entry_title, str) or not vault_entry_title:
        raise ConfigError(
            "rustdesk_setup.vault_entry_title must be a non-empty string"
        )
    service_unit_name = raw.get("service_unit_name")
    if not isinstance(service_unit_name, str) or not service_unit_name:
        raise ConfigError(
            "rustdesk_setup.service_unit_name must be a non-empty string"
        )
    config_dir = raw.get("config_dir")
    if not isinstance(config_dir, str):
        raise ConfigError("rustdesk_setup.config_dir must be a string")
    password_separator = raw.get("password_separator")
    if not isinstance(password_separator, str) or not password_separator:
        raise ConfigError(
            "rustdesk_setup.password_separator must be a non-empty string"
        )
    return RustdeskSetupConfig(
        github_repo=github_repo,
        download_dir=Path(download_dir),
        id_file_path=Path(id_file_path),
        id_file_mode=_octal_mode_field(
            raw.get("id_file_mode"), "rustdesk_setup.id_file_mode"
        ),
        vault_entry_title=vault_entry_title,
        service_unit_name=service_unit_name,
        password_words=_int_field(
            raw.get("password_words"), "rustdesk_setup.password_words"
        ),
        password_separator=password_separator,
        config_dir=Path(config_dir),
        install_timeout_seconds=_int_field(
            raw.get("install_timeout_seconds"),
            "rustdesk_setup.install_timeout_seconds",
        ),
        apt_update_timeout_seconds=_int_field(
            raw.get("apt_update_timeout_seconds"),
            "rustdesk_setup.apt_update_timeout_seconds",
        ),
        install_retries=_int_field(
            raw.get("install_retries"), "rustdesk_setup.install_retries"
        ),
        api_timeout_seconds=_float_field(
            raw.get("api_timeout_seconds"), "rustdesk_setup.api_timeout_seconds"
        ),
        start_check_attempts=_int_field(
            raw.get("start_check_attempts"),
            "rustdesk_setup.start_check_attempts",
        ),
        start_check_retry_delay_seconds=_float_field(
            raw.get("start_check_retry_delay_seconds"),
            "rustdesk_setup.start_check_retry_delay_seconds",
        ),
        options=_rustdesk_options(raw.get("options")),
    )
