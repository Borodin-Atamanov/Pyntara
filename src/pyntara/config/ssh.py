"""[ssh_daemon_setup] and [ssh_client_setup] tables.

The two tables share SshDirective and the directive array parser, so they
live in one module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    ConfigError,
    _float_field,
    _int_field,
    _nonempty_string_field,
    _octal_mode_field,
)


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
    service to become active after a start.
    """

    package_name: str
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
    the task, written through augeas under the Host block so they apply
    to every connection; dropin_file_mode is the file mode of the
    drop-in.
    """

    ssh_config_path: Path
    ssh_config_dropin_path: Path
    dropin_file_mode: int
    directives: tuple[SshDirective, ...]


def _ssh_directives_field(raw: object, name: str) -> tuple[SshDirective, ...]:
    """Validate one directive array of the ssh daemon table.

    A missing array means no directives: the drop-in is then removed
    instead of rendered. Every directive is a table with a unique
    non-empty keyword and a non-empty value string.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be an array of tables")
    directives: list[SshDirective] = []
    seen_names: set[str] = set()
    for index, directive_raw in enumerate(raw):
        if not isinstance(directive_raw, dict):
            raise ConfigError(f"{name} must be an array of tables")
        directive_name = directive_raw.get("name")
        if not isinstance(directive_name, str) or not directive_name:
            raise ConfigError(
                f"{name}[{index}] name must be a non-empty string"
            )
        if directive_name in seen_names:
            raise ConfigError(
                f"{name} directive names must be unique: {directive_name}"
            )
        seen_names.add(directive_name)
        value = directive_raw.get("value")
        if not isinstance(value, str) or not value:
            raise ConfigError(
                f"{name}[{index}] value must be a non-empty string"
            )
        directives.append(SshDirective(name=directive_name, value=value))
    return tuple(directives)


def _ssh_daemon_setup_table(raw: object) -> SshDaemonSetupConfig:
    """Validate the [ssh_daemon_setup] table and build the config.

    package_name, service_unit_name, the key file names and the paths
    are non-empty strings; the timeouts and retries are positive
    integers; start_check_retry_delay_seconds is positive; the file
    modes are octal strings; users is a non-empty array of unique
    non-empty names; directives are validated by
    _ssh_directives_field.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[ssh_daemon_setup] section is missing or not a table")
    package_name = _nonempty_string_field(
        raw.get("package_name"), "ssh_daemon_setup.package_name"
    )
    package_status_timeout_seconds = _int_field(
        raw.get("package_status_timeout_seconds"),
        "ssh_daemon_setup.package_status_timeout_seconds",
    )
    if package_status_timeout_seconds < 1:
        raise ConfigError(
            "ssh_daemon_setup.package_status_timeout_seconds must be positive"
        )
    install_retries = _int_field(
        raw.get("install_retries"), "ssh_daemon_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError("ssh_daemon_setup.install_retries must be positive")
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "ssh_daemon_setup.service_unit_name"
    )
    socket_unit_name = _nonempty_string_field(
        raw.get("socket_unit_name"), "ssh_daemon_setup.socket_unit_name"
    )
    start_check_attempts = _int_field(
        raw.get("start_check_attempts"), "ssh_daemon_setup.start_check_attempts"
    )
    if start_check_attempts < 1:
        raise ConfigError("ssh_daemon_setup.start_check_attempts must be positive")
    start_check_retry_delay_seconds = _float_field(
        raw.get("start_check_retry_delay_seconds"),
        "ssh_daemon_setup.start_check_retry_delay_seconds",
    )
    if start_check_retry_delay_seconds <= 0:
        raise ConfigError(
            "ssh_daemon_setup.start_check_retry_delay_seconds must be positive"
        )
    sshd_config_path = Path(
        _nonempty_string_field(
            raw.get("sshd_config_path"), "ssh_daemon_setup.sshd_config_path"
        )
    )
    sshd_config_dropin_path = Path(
        _nonempty_string_field(
            raw.get("sshd_config_dropin_path"),
            "ssh_daemon_setup.sshd_config_dropin_path",
        )
    )
    private_key_file_name = _nonempty_string_field(
        raw.get("private_key_file_name"),
        "ssh_daemon_setup.private_key_file_name",
    )
    public_key_file_name = _nonempty_string_field(
        raw.get("public_key_file_name"), "ssh_daemon_setup.public_key_file_name"
    )

    def _file_mode_field(name: str) -> int:
        """Parse one octal file mode string like "0700" into an int."""

        return _octal_mode_field(raw.get(name), f"ssh_daemon_setup.{name}")

    users_raw = raw.get("users")
    if not isinstance(users_raw, list) or not users_raw:
        raise ConfigError(
            "ssh_daemon_setup.users must be a non-empty array of strings"
        )
    if not all(isinstance(user, str) and user for user in users_raw):
        raise ConfigError("ssh_daemon_setup.users must be non-empty strings")
    if len(set(users_raw)) != len(users_raw):
        raise ConfigError("ssh_daemon_setup.users must not contain duplicates")
    return SshDaemonSetupConfig(
        package_name=package_name,
        package_status_timeout_seconds=package_status_timeout_seconds,
        install_retries=install_retries,
        service_unit_name=service_unit_name,
        socket_unit_name=socket_unit_name,
        start_check_attempts=start_check_attempts,
        start_check_retry_delay_seconds=start_check_retry_delay_seconds,
        sshd_config_path=Path(sshd_config_path),
        sshd_config_dropin_path=Path(sshd_config_dropin_path),
        dropin_file_mode=_file_mode_field("dropin_file_mode"),
        private_key_file_name=private_key_file_name,
        public_key_file_name=public_key_file_name,
        private_key_file_mode=_file_mode_field("private_key_file_mode"),
        public_key_file_mode=_file_mode_field("public_key_file_mode"),
        authorized_keys_file_mode=_file_mode_field("authorized_keys_file_mode"),
        ssh_dir_mode=_file_mode_field("ssh_dir_mode"),
        root_ssh_dir=Path(
            _nonempty_string_field(
                raw.get("root_ssh_dir"), "ssh_daemon_setup.root_ssh_dir"
            )
        ),
        users=tuple(users_raw),
        directives=_ssh_directives_field(
            raw.get("directives"), "ssh_daemon_setup.directives"
        ),
        port_forwarding_private_key_file_name=_nonempty_string_field(
            raw.get("port_forwarding_private_key_file_name"),
            "ssh_daemon_setup.port_forwarding_private_key_file_name",
        ),
        port_forwarding_public_key_file_name=_nonempty_string_field(
            raw.get("port_forwarding_public_key_file_name"),
            "ssh_daemon_setup.port_forwarding_public_key_file_name",
        ),
        port_forwarding_authorized_keys_options=_nonempty_string_field(
            raw.get("port_forwarding_authorized_keys_options"),
            "ssh_daemon_setup.port_forwarding_authorized_keys_options",
        ),
    )


def _ssh_client_setup_table(raw: object) -> SshClientSetupConfig:
    """Validate the [ssh_client_setup] table and build the config.

    ssh_config_path and ssh_config_dropin_path are non-empty strings;
    dropin_file_mode is an octal string; directives are validated by
    _ssh_directives_field.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[ssh_client_setup] section is missing or not a table"
        )
    ssh_config_path = Path(
        _nonempty_string_field(
            raw.get("ssh_config_path"), "ssh_client_setup.ssh_config_path"
        )
    )
    ssh_config_dropin_path = Path(
        _nonempty_string_field(
            raw.get("ssh_config_dropin_path"),
            "ssh_client_setup.ssh_config_dropin_path",
        )
    )
    dropin_file_mode = _octal_mode_field(
        raw.get("dropin_file_mode"), "ssh_client_setup.dropin_file_mode"
    )
    directives = _ssh_directives_field(
        raw.get("directives"), "ssh_client_setup.directives"
    )
    return SshClientSetupConfig(
        ssh_config_path=ssh_config_path,
        ssh_config_dropin_path=ssh_config_dropin_path,
        dropin_file_mode=dropin_file_mode,
        directives=directives,
    )
