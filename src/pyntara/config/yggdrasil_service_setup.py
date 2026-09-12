"""[yggdrasil_service_setup] table: yggdrasil installation parameters."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class YggdrasilMulticastInterfaceConfig:
    """One multicast interface block of the yggdrasil configuration.

    regex matches interface names; beacon controls whether the node
    advertises its presence; listen controls whether it connects to
    discovered neighbours.
    """

    regex: str
    beacon: bool
    listen: bool


@dataclass(frozen=True)
class YggdrasilServiceSetupConfig:
    """Yggdrasil installation parameters for the yggdrasil_service_setup task.

    The task installs the newest yggdrasil release from github_repo
    (owner/name) as a system service. download_dir is the temporary
    directory for the downloaded package; service_unit_name is the systemd
    unit installed by the package; install_retries is the retry count of
    the package install, so the total attempts are retries plus one.
    config_path is the owned configuration file and private_key_path the
    PEM key file the task extracts from the package-generated config, so
    the node identity survives config rewrites; config_file_mode and
    private_key_file_mode are their file modes. if_name is the TUN
    interface name, if_mtu the interface MTU, admin_listen the admin
    socket URI, listen the inbound listener URIs and
    multicast_interfaces the multicast discovery blocks. The peers
    list comes from the public-peers repository: peers_full_path stores
    the full downloaded list next to the config, peers_tarball_url is the
    repository tarball, peer_batch_size the probe batch size,
    peer_target_count the number of working peers to keep,
    peer_probe_timeout_seconds the wait per batch and peer_max_batches
    the batch cap (0 means the whole list); static_peers is the fallback
    list used when the download fails. address_file_path is the saved
    self address file the task writes once the node is provisioned and
    address_file_mode its file mode; the address is not secret, so the
    file is readable by every user. address_save_retry_base_seconds,
    address_save_retry_multiplier and address_save_retry_max_seconds
    are the geometric backoff of the address save retries: the admin
    socket is not ready immediately after a restart, so the getSelf
    query is repeated while the total retry budget
    address_save_retry_max_seconds lasts.

    asset_name_template is the name of the release asset the task
    installs, with {version} and {arch} as its placeholders, and
    release_tag_prefix the prefix the repository tags carry and the
    version does not. The commands of the tools the task drives are
    values of this section as well: installed_version_command,
    export_key_from_config_command, generate_config_command,
    export_key_from_stdin_command, peers_latency_command,
    self_address_command, journal_connected_query_command,
    service_start_command, service_restart_command,
    service_enable_command, nmcli_reload_command,
    nmcli_connection_show_command, nmcli_connection_delete_command,
    ip_link_show_command and ip_link_delete_command, each carrying the
    name it acts on as a {placeholder}. nm_unmanaged_conf_body is the
    body of the NetworkManager drop-in, netplan_interface_marker the
    line that identifies the netplan YAML of the yggdrasil connection,
    netplan_file_suffix and netplan_backup_suffix the suffixes of that
    file and of its backup, peers_tarball_temp_prefix and
    peers_tarball_temp_suffix the name of the temporary tarball,
    peer_markdown_suffix the suffix of the peer list files,
    line_separator the separator of the lines the task writes and
    config_json_indent the indentation of the rendered configuration.
    config_document_keys names every key of the rendered yggdrasil
    configuration and admin_output_keys every field of the admin socket
    answer, so the schema of both documents is visible in the config
    instead of the task module.
    """

    github_repo: str
    download_dir: Path
    service_unit_name: str
    install_retries: int
    config_path: Path
    private_key_path: Path
    config_file_mode: int
    private_key_file_mode: int
    if_name: str
    if_mtu: int
    admin_listen: str
    listen: tuple[str, ...]
    multicast_interfaces: tuple[YggdrasilMulticastInterfaceConfig, ...]
    peers_full_path: Path
    peers_tarball_url: str
    peer_batch_size: int
    peer_target_count: int
    peer_probe_timeout_seconds: float
    peer_max_batches: int
    static_peers: tuple[str, ...]
    address_file_path: Path
    address_file_mode: int
    address_save_retry_base_seconds: int
    address_save_retry_multiplier: int
    address_save_retry_max_seconds: int
    connection_wait_base_seconds: int
    connection_wait_multiplier: int
    connection_wait_max_seconds: int
    report_channel_name: str
    nm_unmanaged_conf_path: Path
    nm_unmanaged_conf_file_mode: int
    netplan_dir_path: Path
    asset_name_template: str
    release_tag_prefix: str
    installed_version_command: tuple[str, ...]
    export_key_from_config_command: tuple[str, ...]
    generate_config_command: tuple[str, ...]
    export_key_from_stdin_command: tuple[str, ...]
    peers_latency_command: tuple[str, ...]
    self_address_command: tuple[str, ...]
    journal_connected_query_command: tuple[str, ...]
    service_start_command: tuple[str, ...]
    service_restart_command: tuple[str, ...]
    service_enable_command: tuple[str, ...]
    nmcli_reload_command: tuple[str, ...]
    nmcli_connection_show_command: tuple[str, ...]
    nmcli_connection_delete_command: tuple[str, ...]
    ip_link_show_command: tuple[str, ...]
    ip_link_delete_command: tuple[str, ...]
    nm_unmanaged_conf_body: str
    netplan_interface_marker: str
    netplan_file_suffix: str
    netplan_backup_suffix: str
    peers_tarball_temp_prefix: str
    peers_tarball_temp_suffix: str
    peer_markdown_suffix: str
    line_separator: str
    config_json_indent: int
    config_document_keys: dict[str, str]
    admin_output_keys: dict[str, str]
