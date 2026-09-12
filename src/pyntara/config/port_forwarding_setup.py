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
    modes of the askpass helper and of the state file. askpass_display is
    the display ssh-add hands to the askpass helper of the key unlock.
    backoff_base_seconds,
    backoff_multiplier and backoff_max_seconds drive the reconnect pauses.
    state_file_path is the root-only JSON file that records the assigned
    remote ports; the System Metrics collector reads it into the network
    report. service_unit_name and service_restart_seconds configure the
    deployed service unit, whose template and module the task reads from
    service_template_file_name and service_module_name; the four
    systemctl_* commands drive that unit, each carrying the unit name as
    its {service_unit_name} placeholder except the daemon reload, and
    start_check_attempts with start_check_retry_delay_seconds bound the
    loop that decides whether a started service failed. The deployed
    service reads its own commands from the table as well:
    own_addresses_command lists this machine's addresses, agent_start_command
    starts the dedicated agent and key_add_command loads the key into it,
    collector_trigger_command wakes the System Metrics collector with
    collector_trigger_timeout_seconds as its bound, and ssh_forward_command
    is the whole ssh call that holds one reverse tunnel open, with
    {ssh_port}, {key_path}, {remote_port}, {local_port}, {user}, {host},
    {remote_bind_address} and the keepalive and connect bounds
    substituted, so no argument of the tunnel is hidden in the module.
    agent_socket_env_key and agent_pid_env_key name what the service reads
    from the agent output, display_env_key and passphrase_env_key name the
    display and the passphrase variable of the unlock, and askpass_env
    carries the askpass variables with {helper_path} substituted.
    askpass_helper_dir_prefix, askpass_helper_file_name and
    askpass_helper_content describe the helper script that answers the
    unlock with the passphrase variable, and
    forward_outcome_poll_seconds bounds the pause between two reads of the
    ssh output. state_temp_file_suffix and state_json_indent describe how
    the state file is written. journal_identifier and error_priority
    control logging.
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
    askpass_display: str
    state_file_mode: int
    backoff_base_seconds: int
    backoff_multiplier: int
    backoff_max_seconds: int
    state_file_path: Path
    service_unit_name: str
    service_restart_seconds: int
    journal_identifier: str
    service_template_file_name: str
    service_module_name: str
    systemctl_daemon_reload_command: tuple[str, ...]
    systemctl_enable_command: tuple[str, ...]
    systemctl_restart_command: tuple[str, ...]
    systemctl_is_failed_command: tuple[str, ...]
    start_check_attempts: int
    start_check_retry_delay_seconds: float
    own_addresses_command: tuple[str, ...]
    agent_start_command: tuple[str, ...]
    key_add_command: tuple[str, ...]
    collector_trigger_command: tuple[str, ...]
    collector_trigger_timeout_seconds: int
    ssh_forward_command: tuple[str, ...]
    remote_bind_address: str
    agent_socket_env_key: str
    agent_pid_env_key: str
    display_env_key: str
    passphrase_env_key: str
    askpass_env: dict[str, str]
    askpass_helper_dir_prefix: str
    askpass_helper_file_name: str
    askpass_helper_content: str
    forward_outcome_poll_seconds: float
    state_temp_file_suffix: str
    state_json_indent: int
    report_channel_name: str
    error_priority: int
