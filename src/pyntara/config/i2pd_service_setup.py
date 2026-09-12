"""[i2pd_service_setup] table: i2pd installation parameters."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class I2pdServiceSetupConfig:
    """i2pd installation parameters for the i2pd_service_setup task.

    The task installs the newest i2pd release from github_repo (owner/name)
    as a system service and owns the main configuration file. download_dir
    is the temporary directory for the downloaded package;
    service_unit_name is the systemd unit installed by the package;
    config_path is the main configuration file the task writes, and it must
    match the --conf path of the package unit, otherwise the changes are
    ignored; log_level is the i2pd verbosity the checks validate against
    their own list of levels;
    bandwidth is the total router bandwidth limit in kilobytes per second
    and share is the percentage of that bandwidth used for transit
    traffic; http_enabled and socks_proxy_enabled toggle the web console
    and the SOCKS proxy in the rendered configuration;
    socks_proxy_port is the TCP port that proxy listens on, which the
    network telemetry uses to build the ssh command over I2P, so the
    port has one home and no caller guesses the i2pd default;
    install_retries is the
    retry count of the package install, so the total attempts are retries
    plus one; start_check_attempts and start_check_retry_delay_seconds
    bound the loop that waits for the service to become active after a
    start, because a forking service may take a moment to fork.
    tunnels_config_path is the owned tunnels file the task renders with
    the SSH server tunnel and names from the main configuration through
    tunconf, so i2pd reads exactly this file regardless of the package
    default; tunnel_name is the section name of the tunnel;
    tunnel_host is the local address the tunnel forwards to;
    tunnel_keys_path is the identity file of the tunnel destination,
    created by i2pd on the first start, from which the task computes the
    .b32.i2p address. The task saves the computed address into
    address_file_path with the mode address_file_mode, so the deployed
    address command can fall back to the saved value when the keys file
    cannot be decoded. The tunnel port is not configured here: it is
    read from the ssh_daemon_setup Port directive, so the tunnel and the
    SSH daemon can never diverge.
    codename_asset_name_template and generic_asset_name_template are the
    two candidate names of the .deb asset of a release, formatted with
    the release tag, the codename read under os_release_codename_key and
    the dpkg architecture: the codename-specific build wins and the
    generic build is the fallback. version_command prints the installed
    version; service_enable_command, service_start_command and
    service_restart_command drive the unit and carry the unit name as
    their {service_unit_name} placeholder. config_template_file_name and
    tunnels_template_file_name name the two templates under task_data/ of
    the clone, and config_true_value and config_false_value are the
    boolean spelling the rendered configuration uses.
    address_check_attempts and address_check_retry_delay_seconds bound
    the loop that waits for the tunnel identity file after the first
    start, so the run reports the address instead of leaving it to the
    next run.
    """

    github_repo: str
    download_dir: Path
    service_unit_name: str
    os_release_file_path: Path
    config_path: Path
    log_level: str
    bandwidth: int
    share: int
    http_enabled: bool
    socks_proxy_enabled: bool
    socks_proxy_port: int
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    tunnels_config_path: Path
    tunnel_name: str
    tunnel_host: str
    tunnel_keys_path: Path
    address_file_path: Path
    address_file_mode: int
    codename_asset_name_template: str
    generic_asset_name_template: str
    os_release_codename_key: str
    version_command: tuple[str, ...]
    service_enable_command: tuple[str, ...]
    service_start_command: tuple[str, ...]
    service_restart_command: tuple[str, ...]
    config_template_file_name: str
    tunnels_template_file_name: str
    config_true_value: str
    config_false_value: str
    address_check_attempts: int
    address_check_retry_delay_seconds: float
