"""[tor_setup] table: Tor installation parameters."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    address is not secret, so the file is readable by every user, and
    hostname_file_name is the name Tor writes the onion hostname under
    inside hidden_service_dir. The drop-in is rendered from the template
    dropin_template_file_name under task_data/tor_setup/ of the clone,
    and include_directive is the directive that pulls it into the main
    configuration. verify_config_command, service_enable_command,
    service_start_command and service_restart_command are the commands
    the task runs, with {tor_user} and {service_unit_name} substituted.
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
    dropin_template_file_name: str
    include_directive: str
    hostname_file_name: str
    verify_config_command: tuple[str, ...]
    service_enable_command: tuple[str, ...]
    service_start_command: tuple[str, ...]
    service_restart_command: tuple[str, ...]
    report_channel_name: str
    install_retries: int
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    address_file_path: Path
    address_file_mode: int
