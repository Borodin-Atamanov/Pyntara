"""[engine] table: engine-wide runtime values."""


from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class EngineConfig:
    """Engine-wide runtime values from the [engine] table.

    desktop_detect_processes are the process names whose presence marks a
    desktop session in the default mode detection; the list lives here so
    the detection is configurable without code changes. systemd_unit_dir is
    the one directory the tasks that deploy a systemd unit write it to.
    github_latest_release_url is the endpoint of every release query, a
    template whose {repo} is replaced by the repository of the task.
    curl_download_command is the curl call that downloads one URL into one
    file, its {output_path} replaced by the file the transfer writes and
    its {write_out} replaced by curl_download_write_out, the summary curl
    prints after a transfer; curl_query_command is the curl call that
    fetches one metadata answer as text; the shared helper of both inserts
    the retry and timeout flags of the curl settings above before the URL,
    so a task never spells the flags itself. curl_parallel_command is the
    call that queries several URLs in one process, its {parallel_max},
    {timeout_seconds} and {write_out} filled by the shared helper, and
    curl_parallel_source_marker is the token curl_parallel_write_out prints
    before the effective URL of each transfer, by which the merged output
    is split back into one answer per service. report_json_indent is the
    indentation of every JSON document an address command prints;
    ssh_report_command_format is the ssh command a report record carries,
    with {port}, {address} and {proxy_option} filled by the shared
    builder, ssh_report_proxy_option_format is the proxy option of an
    anonymity channel, ssh_report_socks_command_format is the netcat
    command that routes a connection through the local SOCKS proxy and
    ssh_report_proxy_host is the host of that proxy; report_record_keys
    are the field names of a record, by the meaning of each field, so the
    commands and the collector agree on the shape in one place. os_release_family_keys are the
    fields of the distribution identity file that name the distribution and
    os_release_debian_family_names are the values of those fields that mean
    a Debian-based system, which the shared os_family_is_debian helper
    reads.
    system_python is the interpreter of the managed system, used by a task
    that runs an embedded client against the system packages.
    journal_identifier is the name under which the engine mirrors its own
    messages into the system journal, journal_command is the command that
    writes one line under that name and journal_priority_command the same
    for a line that carries its own priority; the composition root hands
    the table to the journal writer before the first message.
    process_check_command answers whether a process with an exact name is
    running, its {process_name} replaced by the name, and
    process_check_timeout_seconds bounds that query. root_owner_uid and
    root_owner_gid are the owner the shared apply_owner helper gives
    a file the run creates as root. percent_scale is the scale that turns
    a fraction into a percent, and bytes_per_kib with bytes_per_mib are
    the byte counts of a kibibyte and of a mebibyte. desktop_username is
    the account
    of the desktop user whose live session the run reaches;
    session_environment_command prints that session's environment, one
    KEY=VALUE per line, with {username} replaced by desktop_username;
    session_environment_keys are the session variables the run exports to
    every task and every child process; session_bus_key is the bus variable of
    that session and session_display_keys are its display variables, the two
    groups that decide whether a session counts as live.
    """

    task_data_root: Path
    systemd_unit_dir: Path
    notice_timeout: int
    command_timeout_seconds: int
    curl_timeout_seconds: int
    curl_download_timeout_seconds: int
    curl_retries: int
    curl_retry_delay_seconds: int
    curl_connect_timeout_seconds: int
    curl_retry_max_time_seconds: int
    curl_download_command: tuple[str, ...]
    curl_download_write_out: str
    curl_query_command: tuple[str, ...]
    curl_parallel_command: tuple[str, ...]
    curl_parallel_write_out: str
    curl_parallel_source_marker: str
    report_json_indent: int
    ssh_report_command_format: str
    ssh_report_proxy_option_format: str
    ssh_report_socks_command_format: str
    ssh_report_proxy_host: str
    report_record_keys: dict[str, str]
    github_latest_release_url: str
    github_release_download_url: str
    release_asset_architectures: dict[str, str]
    partial_download_file_suffix: str
    os_release_family_keys: tuple[str, ...]
    os_release_debian_family_names: tuple[str, ...]
    system_python: str
    journal_identifier: str
    journal_command: tuple[str, ...]
    journal_priority_command: tuple[str, ...]
    root_owner_uid: int
    root_owner_gid: int
    percent_scale: int
    bytes_per_kib: int
    bytes_per_mib: int
    error_priority: int
    progress_priority: int
    process_check_timeout_seconds: int
    process_check_command: tuple[str, ...]
    task_start_delay_seconds: float
    desktop_detect_processes: tuple[str, ...]
    desktop_username: str = ""
    session_environment_command: tuple[str, ...] = ()
    session_environment_keys: tuple[str, ...] = ()
    session_bus_key: str = ""
    session_display_keys: tuple[str, ...] = ()
    upnpc_status_command: tuple[str, ...] = ()
    upnpc_mapping_list_command: tuple[str, ...] = ()
    upnpc_mapping_add_command: tuple[str, ...] = ()
    upnpc_external_address_key: str = ""
    upnpc_protocol_names: tuple[str, ...] = ()
    upnpc_mapping_arrow: str = ""
    local_addresses_command: tuple[str, ...] = ()
    directly_connected_networks_command: tuple[str, ...] = ()
    default_route_command: tuple[str, ...] = ()
    augtool_command: tuple[str, ...] = ()
    augeas_files_node_prefix: str = ""
    interface_addresses_command: tuple[str, ...] = ()
    address_family_by_flag: dict[str, str] = field(default_factory=dict)
    iproute2_address_family_names: dict[str, str] = field(default_factory=dict)
    link_scope_name: str = ""
    dpkg_architecture_command: tuple[str, ...] = ()
    package_status_query_command: tuple[str, ...] = ()
    apt_update_command: tuple[str, ...] = ()
    apt_install_command: tuple[str, ...] = ()
    apt_noninteractive_environment: dict[str, str] = field(default_factory=dict)
    systemctl_is_enabled_command: tuple[str, ...] = ()
    systemctl_is_active_command: tuple[str, ...] = ()
    systemd_enabled_states: tuple[str, ...] = ()
    systemd_active_state: str = ""
    socket_listener_command: tuple[str, ...] = ()
    systemctl_main_pid_command: tuple[str, ...] = ()
    systemctl_stop_command: tuple[str, ...] = ()
