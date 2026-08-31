"""[port_forwarding_setup] table parser.

The section carries the parameters of the auto port forwarding task and
service: the vault group with the server addresses, the vault entry with
the port-forwarding key passphrase, the desired remote port range derived
from the hostname, the ssh connection options, the reconnect backoff, the
state and telemetry file names and the deployed service unit parameters.
The forwarded local port itself is not configured here: it is the SSH
daemon port read from the ssh_daemon_setup directives through the shared
ssh helper, the single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    ConfigError,
    _int_field,
    _nonempty_string_field,
)


@dataclass(frozen=True)
class PortForwardingSetupConfig:
    """Auto port forwarding parameters for the port_forwarding_setup task.

    vault_group_title names the vault subgroup that carries the
    port-forwarding server addresses in the url field of each entry (the
    [vault_structure] groups); passphrase_entry_title names the vault
    entry that carries the passphrase of the deployed port-forwarding
    private key. remote_ssh_user is the user the service connects as on
    every server. desired_port_min and desired_port_max bound the
    deterministic desired remote port derived from the hostname.
    server_alive_interval_seconds, server_alive_count_max and
    connect_timeout_seconds tune the ssh connection; backoff_base_seconds,
    backoff_multiplier and backoff_max_seconds drive the reconnect pauses.
    state_file_path is the root-only JSON file that records the assigned
    remote ports; the System Metrics collector reads it into the network
    report. service_unit_name and service_restart_seconds configure the
    deployed service unit; journal_identifier and error_priority control
    logging.
    """

    vault_group_title: str
    passphrase_entry_title: str
    remote_ssh_user: str
    desired_port_min: int
    desired_port_max: int
    server_alive_interval_seconds: int
    server_alive_count_max: int
    connect_timeout_seconds: int
    backoff_base_seconds: int
    backoff_multiplier: int
    backoff_max_seconds: int
    state_file_path: Path
    service_unit_name: str
    service_restart_seconds: int
    journal_identifier: str
    error_priority: int


def _positive_int_field(raw: object, name: str) -> int:
    """Validate a positive integer config value."""

    value = _int_field(raw, name)
    if value <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _port_field(raw: object, name: str) -> int:
    """Validate a TCP port config value from 1 to 65535."""

    value = _int_field(raw, name)
    if not 1 <= value <= 65535:
        raise ConfigError(f"{name} must be a TCP port from 1 to 65535")
    return value


def _port_forwarding_setup_table(raw: object) -> PortForwardingSetupConfig:
    """Validate the [port_forwarding_setup] table and build the config.

    Every value is required and typed. The desired port range must be
    ordered and inside the TCP port space; the backoff values are
    positive integers, the reconnect pause grows geometrically by the
    multiplier until the ceiling; error_priority is an integer 0-7.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[port_forwarding_setup] section is missing or not a table"
        )
    section = "port_forwarding_setup."
    vault_group_title = _nonempty_string_field(
        raw.get("vault_group_title"), section + "vault_group_title"
    )
    passphrase_entry_title = _nonempty_string_field(
        raw.get("passphrase_entry_title"), section + "passphrase_entry_title"
    )
    remote_ssh_user = _nonempty_string_field(
        raw.get("remote_ssh_user"), section + "remote_ssh_user"
    )
    desired_port_min = _port_field(
        raw.get("desired_port_min"), section + "desired_port_min"
    )
    desired_port_max = _port_field(
        raw.get("desired_port_max"), section + "desired_port_max"
    )
    if desired_port_min > desired_port_max:
        raise ConfigError(
            "port_forwarding_setup.desired_port_min must not exceed "
            "desired_port_max"
        )
    server_alive_interval_seconds = _positive_int_field(
        raw.get("server_alive_interval_seconds"),
        section + "server_alive_interval_seconds",
    )
    server_alive_count_max = _positive_int_field(
        raw.get("server_alive_count_max"), section + "server_alive_count_max"
    )
    connect_timeout_seconds = _positive_int_field(
        raw.get("connect_timeout_seconds"), section + "connect_timeout_seconds"
    )
    backoff_base_seconds = _positive_int_field(
        raw.get("backoff_base_seconds"), section + "backoff_base_seconds"
    )
    backoff_multiplier = _positive_int_field(
        raw.get("backoff_multiplier"), section + "backoff_multiplier"
    )
    backoff_max_seconds = _positive_int_field(
        raw.get("backoff_max_seconds"), section + "backoff_max_seconds"
    )
    state_file_path = Path(
        _nonempty_string_field(raw.get("state_file_path"), section + "state_file_path")
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), section + "service_unit_name"
    )
    service_restart_seconds = _positive_int_field(
        raw.get("service_restart_seconds"), section + "service_restart_seconds"
    )
    journal_identifier = _nonempty_string_field(
        raw.get("journal_identifier"), section + "journal_identifier"
    )
    error_priority = _int_field(raw.get("error_priority"), section + "error_priority")
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "port_forwarding_setup.error_priority must be between 0 and 7"
        )
    return PortForwardingSetupConfig(
        vault_group_title=vault_group_title,
        passphrase_entry_title=passphrase_entry_title,
        remote_ssh_user=remote_ssh_user,
        desired_port_min=desired_port_min,
        desired_port_max=desired_port_max,
        server_alive_interval_seconds=server_alive_interval_seconds,
        server_alive_count_max=server_alive_count_max,
        connect_timeout_seconds=connect_timeout_seconds,
        backoff_base_seconds=backoff_base_seconds,
        backoff_multiplier=backoff_multiplier,
        backoff_max_seconds=backoff_max_seconds,
        state_file_path=state_file_path,
        service_unit_name=service_unit_name,
        service_restart_seconds=service_restart_seconds,
        journal_identifier=journal_identifier,
        error_priority=error_priority,
    )
