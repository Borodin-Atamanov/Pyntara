"""[i2pd_service_setup] table: i2pd installation parameters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    I2PD_LOG_LEVELS,
    ConfigError,
    _float_field,
    _int_field,
    _nonempty_string_field,
)


@dataclass(frozen=True)
class I2pdServiceSetupConfig:
    """i2pd installation parameters for the i2pd_service_setup task.

    The task installs the newest i2pd release from github_repo (owner/name)
    as a system service and owns the main configuration file. download_dir
    is the temporary directory for the downloaded package;
    service_unit_name is the systemd unit installed by the package;
    config_path is the main configuration file the task writes, and it must
    match the --conf path of the package unit, otherwise the changes are
    ignored; log_level is the i2pd verbosity from I2PD_LOG_LEVELS;
    http_enabled and socks_proxy_enabled toggle the web console and the
    SOCKS proxy in the rendered configuration; install_retries is the
    retry count of the package install, so the total attempts are retries
    plus one; start_check_attempts and start_check_retry_delay_seconds
    bound the loop that waits for the service to become active after a
    start, because a forking service may take a moment to fork.
    """

    github_repo: str
    download_dir: Path
    service_unit_name: str
    config_path: Path
    log_level: str
    http_enabled: bool
    socks_proxy_enabled: bool
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float


def _i2pd_service_setup_table(raw: object) -> I2pdServiceSetupConfig:
    """Validate the [i2pd_service_setup] table and build the config.

    github_repo, download_dir, service_unit_name and config_path are
    non-empty strings; log_level is one of the I2PD_LOG_LEVELS values;
    http_enabled and socks_proxy_enabled are strict booleans;
    install_retries and start_check_attempts are positive integers;
    start_check_retry_delay_seconds is positive, so the readiness loop
    always waits between attempts.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[i2pd_service_setup] section is missing or not a table")
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "i2pd_service_setup.github_repo"
    )
    download_dir = Path(
        _nonempty_string_field(
            raw.get("download_dir"), "i2pd_service_setup.download_dir"
        )
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "i2pd_service_setup.service_unit_name"
    )
    config_path = Path(
        _nonempty_string_field(
            raw.get("config_path"), "i2pd_service_setup.config_path"
        )
    )
    log_level = raw.get("log_level")
    if log_level not in I2PD_LOG_LEVELS:
        raise ConfigError(
            "i2pd_service_setup.log_level must be one of "
            + ", ".join(I2PD_LOG_LEVELS)
        )
    http_enabled = raw.get("http_enabled")
    if not isinstance(http_enabled, bool):
        raise ConfigError("i2pd_service_setup.http_enabled must be a boolean")
    socks_proxy_enabled = raw.get("socks_proxy_enabled")
    if not isinstance(socks_proxy_enabled, bool):
        raise ConfigError(
            "i2pd_service_setup.socks_proxy_enabled must be a boolean"
        )
    install_retries = _int_field(
        raw.get("install_retries"), "i2pd_service_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("i2pd_service_setup.install_retries must be positive")
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "i2pd_service_setup.start_check_attempts"
    )
    if start_check_attempts < 1:
        raise ConfigError(
            "i2pd_service_setup.start_check_attempts must be positive"
        )
    start_check_retry_delay_seconds = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "i2pd_service_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "i2pd_service_setup.start_check_retry_delay_seconds must be positive"
        )
    return I2pdServiceSetupConfig(
        github_repo=github_repo,
        download_dir=download_dir,
        service_unit_name=service_unit_name,
        config_path=config_path,
        log_level=log_level,
        http_enabled=http_enabled,
        socks_proxy_enabled=socks_proxy_enabled,
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
    )
