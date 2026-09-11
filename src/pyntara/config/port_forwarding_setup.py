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
    connect_timeout_seconds tune the ssh connection;
    own_addresses_timeout_seconds bounds the ip call that lists this
    machine's own addresses, and agent_start_timeout_seconds and
    key_unlock_timeout_seconds bound the ssh-agent start and the key
    unlock, and askpass_helper_file_mode and state_file_mode carry the
    modes of the askpass helper and of the state file. backoff_base_seconds,
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
    own_addresses_timeout_seconds: int
    agent_start_timeout_seconds: int
    key_unlock_timeout_seconds: int
    askpass_helper_file_mode: int
    state_file_mode: int
    backoff_base_seconds: int
    backoff_multiplier: int
    backoff_max_seconds: int
    state_file_path: Path
    service_unit_name: str
    service_restart_seconds: int
    journal_identifier: str
    error_priority: int
