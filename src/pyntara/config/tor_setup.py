"""[tor_setup] table: Tor installation parameters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    TOR_LOG_LEVELS,
    ConfigError,
    _float_field,
    _int_field,
    _nonempty_string_field,
    _octal_mode_field,
)


@dataclass(frozen=True)
class TorSetupConfig:
    """Tor installation parameters for the tor_setup task.

    The task installs package_name from the Ubuntu archive and runs
    service_unit_name as a system service. The Ubuntu package uses the
    multi-instance design: service_unit_name is the daemon instance
    tor@default.service, not the empty master unit tor.service. The
    task never rewrites the main configuration file at torrc_path: it
    only guarantees the %include line named by torrc_include_path
    through the shared add_line_to_file helper, so unrelated content of
    the file survives. The included value is a plain file path directly
    in the /etc/tor directory: the AppArmor profile of the package
    allows reading /etc/tor/* but not its subdirectories, and a plain
    path avoids the directory listing a glob would need. The owned
    settings are rendered into the drop-in at torrc_dropin_path
    (written with dropin_file_mode): the log level log_level, the SOCKS
    proxy port socks_port and the onion service that forwards to the
    local SSH daemon. hidden_service_dir is the directory of the onion
    service identity, created by the task with hidden_service_dir_mode
    and owned by tor_user, so Tor can write the keys and the hostname
    file; the identity must never be recreated, otherwise the address
    changes. onion_ssh_port is the virtual port clients connect to; the
    local port is not configured here, it is read from the
    ssh_daemon_setup Port directive, so the forward and the SSH daemon
    never diverge. num_introduction_points is the number of introduction
    points of the service. install_retries is the retry count of the
    package install, so the total attempts are retries plus one;
    start_check_attempts and start_check_retry_delay_seconds bound the
    loop that waits for the service to become active after a start.
    address_file_path is the saved onion address file the task writes
    once the hostname file exists and address_file_mode its mode; the
    address is not secret, so the file is readable by every user.
    """

    package_name: str
    service_unit_name: str
    torrc_path: Path
    torrc_dropin_path: Path
    torrc_include_path: str
    dropin_file_mode: int
    hidden_service_dir: Path
    hidden_service_dir_mode: int
    tor_user: str
    socks_port: int
    onion_ssh_port: int
    num_introduction_points: int
    log_level: str
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    address_file_path: Path
    address_file_mode: int


def _tor_setup_table(raw: object) -> TorSetupConfig:
    """Validate the [tor_setup] table and build the config.

    package_name, service_unit_name, torrc_path, torrc_dropin_path,
    torrc_include_path, hidden_service_dir and tor_user are non-empty
    strings; dropin_file_mode, hidden_service_dir_mode and
    address_file_mode are octal strings; log_level is one of the
    TOR_LOG_LEVELS values; socks_port and onion_ssh_port are port numbers
    between 1 and 65535; num_introduction_points, install_retries and
    start_check_attempts are positive integers;
    start_check_retry_delay_seconds is positive, so the readiness loop
    always waits between attempts; address_file_path is a non-empty
    string.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[tor_setup] section is missing or not a table")
    package_name = _nonempty_string_field(
        raw.get("package_name"), "tor_setup.package_name"
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "tor_setup.service_unit_name"
    )
    torrc_path = Path(
        _nonempty_string_field(raw.get("torrc_path"), "tor_setup.torrc_path")
    )
    torrc_dropin_path = Path(
        _nonempty_string_field(
            raw.get("torrc_dropin_path"), "tor_setup.torrc_dropin_path"
        )
    )
    torrc_include_path = _nonempty_string_field(
        raw.get("torrc_include_path"), "tor_setup.torrc_include_path"
    )
    dropin_file_mode = _octal_mode_field(
        raw.get("dropin_file_mode"), "tor_setup.dropin_file_mode"
    )
    hidden_service_dir = Path(
        _nonempty_string_field(
            raw.get("hidden_service_dir"), "tor_setup.hidden_service_dir"
        )
    )
    hidden_service_dir_mode = _octal_mode_field(
        raw.get("hidden_service_dir_mode"),
        "tor_setup.hidden_service_dir_mode",
    )
    tor_user = _nonempty_string_field(
        raw.get("tor_user"), "tor_setup.tor_user"
    )
    socks_port = _int_field(raw.get("socks_port"), "tor_setup.socks_port")
    if not 1 <= socks_port <= 65535:
        raise ConfigError("tor_setup.socks_port must be between 1 and 65535")
    onion_ssh_port = _int_field(
        raw.get("onion_ssh_port"), "tor_setup.onion_ssh_port"
    )
    if not 1 <= onion_ssh_port <= 65535:
        raise ConfigError(
            "tor_setup.onion_ssh_port must be between 1 and 65535"
        )
    num_introduction_points = _int_field(
        raw.get("num_introduction_points"),
        "tor_setup.num_introduction_points",
    )
    if num_introduction_points < 1:
        raise ConfigError(
            "tor_setup.num_introduction_points must be positive"
        )
    log_level = _nonempty_string_field(
        raw.get("log_level"), "tor_setup.log_level"
    )
    if log_level not in TOR_LOG_LEVELS:
        raise ConfigError(
            f"tor_setup.log_level must be one of {', '.join(TOR_LOG_LEVELS)}"
        )
    install_retries = _int_field(
        raw.get("install_retries"), "tor_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("tor_setup.install_retries must be positive")
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "tor_setup.start_check_attempts"
    )
    if start_check_attempts < 1:
        raise ConfigError(
            "tor_setup.start_check_attempts must be positive"
        )
    start_check_retry_delay_seconds = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "tor_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "tor_setup.start_check_retry_delay_seconds must be positive"
        )
    address_file_path = Path(
        _nonempty_string_field(
            raw.get("address_file_path"), "tor_setup.address_file_path"
        )
    )
    address_file_mode = _octal_mode_field(
        raw.get("address_file_mode"), "tor_setup.address_file_mode"
    )
    return TorSetupConfig(
        package_name=package_name,
        service_unit_name=service_unit_name,
        torrc_path=torrc_path,
        torrc_dropin_path=torrc_dropin_path,
        torrc_include_path=torrc_include_path,
        dropin_file_mode=dropin_file_mode,
        hidden_service_dir=hidden_service_dir,
        hidden_service_dir_mode=hidden_service_dir_mode,
        tor_user=tor_user,
        socks_port=socks_port,
        onion_ssh_port=onion_ssh_port,
        num_introduction_points=num_introduction_points,
        log_level=log_level,
        install_retries=install_retries,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        address_file_path=address_file_path,
        address_file_mode=address_file_mode,
    )
