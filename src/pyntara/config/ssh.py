"""[ssh_daemon_setup] and [ssh_client_setup] tables.

The two tables share SshDirective and the directive array parser, so they
live in one module.
"""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SshDirective:
    """One sshd_config directive: a keyword and its value.

    The value is kept as a single string and joined as-is into the
    rendered drop-in, so the directive spelling stays exactly as
    configured.
    """

    name: str
    value: str


@dataclass(frozen=True)
class SshDaemonSetupConfig:
    """SSH server parameters for the ssh_daemon_setup task.

    The task installs package_name and runs service_unit_name; the
    sshd configuration is patched through the drop-in at
    sshd_config_dropin_path, never through sshd_config_path itself,
    which is only checked for an Include directive that pulls the
    drop-in directory in. private_key_file_name and public_key_file_name
    are the repository key file names under task_data/ssh_daemon_setup/;
    the private key is deployed as-is, still encrypted with its pass
    phrase. The keys are deployed to root (root_ssh_dir) and to every
    user of users, using ssh_dir_mode for the .ssh directories,
    private_key_file_mode and public_key_file_mode for the key files and
    authorized_keys_file_mode for the authorized_keys file; the public
    key is appended to authorized_keys without duplicates. directives
    are the sshd_config keywords guaranteed by the task, rendered into
    the drop-in in order. The port-forwarding key pair is deployed the
    same way in parallel with the main pair: its file names come from
    port_forwarding_private_key_file_name and
    port_forwarding_public_key_file_name, and its public key line in
    authorized_keys carries port_forwarding_authorized_keys_options,
    the restriction prefix that permits only port forwarding.
    package_status_timeout_seconds bounds the
    dpkg status query, install_retries is the retry count of the
    package install, start_check_attempts and
    start_check_retry_delay_seconds bound the loop that waits for the
    service to become active after a start. augeas_tools_package_name
    names the package that provides augtool, which the task installs
    itself when the tool is missing.
    """

    package_name: str
    augeas_tools_package_name: str
    package_status_timeout_seconds: int
    install_retries: int
    service_unit_name: str
    socket_unit_name: str
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    sshd_config_path: Path
    sshd_config_dropin_path: Path
    dropin_file_mode: int
    private_key_file_name: str
    public_key_file_name: str
    private_key_file_mode: int
    public_key_file_mode: int
    authorized_keys_file_mode: int
    ssh_dir_mode: int
    root_ssh_dir: Path
    users: tuple[str, ...]
    directives: tuple[SshDirective, ...]
    port_forwarding_private_key_file_name: str
    port_forwarding_public_key_file_name: str
    port_forwarding_authorized_keys_options: str


@dataclass(frozen=True)
class SshClientSetupConfig:
    """System-wide SSH client parameters for the ssh_client_setup task.

    The client configuration is patched through the drop-in at
    ssh_config_dropin_path, never through ssh_config_path itself, which
    is only checked for an Include directive that pulls the drop-in
    directory in. directives are the ssh_config keywords guaranteed by
    the task, written through augeas under the container block the
    config names, so they apply to every connection; dropin_file_mode is
    the file mode of the drop-in and dropin_header is the ownership
    comment written at its top, without the leading hash. augeas_lens is
    the lens and augeas_container with augeas_container_value name the
    node the directives live under. augeas_tools_package_name names the
    package that provides augtool, which the task installs itself when
    the tool is missing; package_status_timeout_seconds bounds the dpkg
    status query and install_retries is the retry count of the package
    install.
    """

    ssh_config_path: Path
    ssh_config_dropin_path: Path
    dropin_file_mode: int
    dropin_header: str
    augeas_lens: str
    augeas_container: str
    augeas_container_value: str
    augeas_tools_package_name: str
    package_status_timeout_seconds: int
    install_retries: int
    directives: tuple[SshDirective, ...]
