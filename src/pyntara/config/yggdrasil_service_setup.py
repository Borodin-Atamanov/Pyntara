"""[yggdrasil_service_setup] table: yggdrasil installation parameters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import (
    YGGDRASIL_LISTEN_SCHEMES,
    YGGDRASIL_PEER_SCHEMES,
    ConfigError,
    _float_field,
    _int_field,
    _nonempty_string_field,
    _octal_mode_field,
)


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


def _yggdrasil_uri_list_field(
    raw: object, name: str, schemes: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate an array of yggdrasil URIs with allowed schemes.

    A missing array means an empty list. Every entry is a non-empty
    string whose scheme is in schemes; the scheme is everything before
    the first colon, so a malformed URI without a colon is rejected.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be an array of strings")
    result: list[str] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, str) or not entry:
            raise ConfigError(f"{name}[{index}] must be a non-empty string")
        scheme = entry.split(":", 1)[0].casefold()
        if scheme not in schemes:
            raise ConfigError(
                f"{name}[{index}] scheme {scheme} is not supported, "
                f"allowed: {', '.join(schemes)}"
            )
        result.append(entry)
    return tuple(result)


def _yggdrasil_multicast_field(
    raw: object, name: str
) -> tuple[YggdrasilMulticastInterfaceConfig, ...]:
    """Validate the multicast_interfaces array of the yggdrasil table.

    A missing array means no multicast discovery. Every entry is a table
    with a non-empty regex string and strict boolean beacon and listen
    switches.
    """

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be an array of tables")
    result: list[YggdrasilMulticastInterfaceConfig] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"{name} must be an array of tables")
        regex = entry.get("regex")
        if not isinstance(regex, str) or not regex:
            raise ConfigError(f"{name}[{index}] regex must be a non-empty string")
        beacon = entry.get("beacon")
        if not isinstance(beacon, bool):
            raise ConfigError(f"{name}[{index}] beacon must be a boolean")
        listen = entry.get("listen")
        if not isinstance(listen, bool):
            raise ConfigError(f"{name}[{index}] listen must be a boolean")
        result.append(
            YggdrasilMulticastInterfaceConfig(
                regex=regex, beacon=beacon, listen=listen
            )
        )
    return tuple(result)


def _yggdrasil_service_setup_table(raw: object) -> YggdrasilServiceSetupConfig:
    """Validate the [yggdrasil_service_setup] table and build the config.

    github_repo, download_dir, service_unit_name, config_path,
    private_key_path, if_name, admin_listen and peers_tarball_url are
    non-empty strings; install_retries, if_mtu, peer_batch_size and
    peer_target_count are positive integers; if_mtu stays within the
    yggdrasil range; config_file_mode, private_key_file_mode and
    address_file_mode are octal strings; listen and static_peers are URI
    arrays with the allowed schemes; multicast_interfaces is the
    multicast block array; peer_probe_timeout_seconds is positive and
    peer_max_batches is non-negative; address_file_path is a non-empty
    string; address_save_retry_base_seconds is positive,
    address_save_retry_multiplier is at least 2 and
    address_save_retry_max_seconds is not below the base.
    """

    if not isinstance(raw, dict):
        raise ConfigError(
            "[yggdrasil_service_setup] section is missing or not a table"
        )
    github_repo = _nonempty_string_field(
        raw.get("github_repo"), "yggdrasil_service_setup.github_repo"
    )
    download_dir = Path(
        _nonempty_string_field(
            raw.get("download_dir"), "yggdrasil_service_setup.download_dir"
        )
    )
    service_unit_name = _nonempty_string_field(
        raw.get("service_unit_name"), "yggdrasil_service_setup.service_unit_name"
    )
    install_retries = _int_field(
        raw.get("install_retries"), "yggdrasil_service_setup.install_retries"
    )
    if install_retries < 1:
        raise ConfigError(
            "yggdrasil_service_setup.install_retries must be positive"
        )
    config_path = Path(
        _nonempty_string_field(
            raw.get("config_path"), "yggdrasil_service_setup.config_path"
        )
    )
    private_key_path = Path(
        _nonempty_string_field(
            raw.get("private_key_path"), "yggdrasil_service_setup.private_key_path"
        )
    )
    config_file_mode = _octal_mode_field(
        raw.get("config_file_mode"), "yggdrasil_service_setup.config_file_mode"
    )
    private_key_file_mode = _octal_mode_field(
        raw.get("private_key_file_mode"),
        "yggdrasil_service_setup.private_key_file_mode",
    )
    if_name = _nonempty_string_field(
        raw.get("if_name"), "yggdrasil_service_setup.if_name"
    )
    if_mtu = _int_field(raw.get("if_mtu"), "yggdrasil_service_setup.if_mtu")
    if not 1280 <= if_mtu <= 65535:
        raise ConfigError(
            "yggdrasil_service_setup.if_mtu must be between 1280 and 65535"
        )
    admin_listen = _nonempty_string_field(
        raw.get("admin_listen"), "yggdrasil_service_setup.admin_listen"
    )
    listen = _yggdrasil_uri_list_field(
        raw.get("listen"), "yggdrasil_service_setup.listen", YGGDRASIL_LISTEN_SCHEMES
    )
    multicast_interfaces = _yggdrasil_multicast_field(
        raw.get("multicast_interfaces"),
        "yggdrasil_service_setup.multicast_interfaces",
    )
    peers_full_path = Path(
        _nonempty_string_field(
            raw.get("peers_full_path"), "yggdrasil_service_setup.peers_full_path"
        )
    )
    peers_tarball_url = _nonempty_string_field(
        raw.get("peers_tarball_url"), "yggdrasil_service_setup.peers_tarball_url"
    )
    peer_batch_size = _int_field(
        raw.get("peer_batch_size"), "yggdrasil_service_setup.peer_batch_size"
    )
    if peer_batch_size < 1:
        raise ConfigError(
            "yggdrasil_service_setup.peer_batch_size must be positive"
        )
    peer_target_count = _int_field(
        raw.get("peer_target_count"), "yggdrasil_service_setup.peer_target_count"
    )
    if peer_target_count < 1:
        raise ConfigError(
            "yggdrasil_service_setup.peer_target_count must be positive"
        )
    peer_probe_timeout_seconds = _float_field(
        raw.get("peer_probe_timeout_seconds"),
        "yggdrasil_service_setup.peer_probe_timeout_seconds",
    )
    if peer_probe_timeout_seconds <= 0:
        raise ConfigError(
            "yggdrasil_service_setup.peer_probe_timeout_seconds must be positive"
        )
    peer_max_batches = _int_field(
        raw.get("peer_max_batches"), "yggdrasil_service_setup.peer_max_batches"
    )
    if peer_max_batches < 0:
        raise ConfigError(
            "yggdrasil_service_setup.peer_max_batches must not be negative"
        )
    static_peers = _yggdrasil_uri_list_field(
        raw.get("static_peers"),
        "yggdrasil_service_setup.static_peers",
        YGGDRASIL_PEER_SCHEMES,
    )
    address_file_path = Path(
        _nonempty_string_field(
            raw.get("address_file_path"), "yggdrasil_service_setup.address_file_path"
        )
    )
    address_file_mode = _octal_mode_field(
        raw.get("address_file_mode"), "yggdrasil_service_setup.address_file_mode"
    )
    address_save_retry_base_seconds = _int_field(
        raw.get("address_save_retry_base_seconds"),
        "yggdrasil_service_setup.address_save_retry_base_seconds",
    )
    if address_save_retry_base_seconds < 1:
        raise ConfigError(
            "yggdrasil_service_setup.address_save_retry_base_seconds must be positive"
        )
    address_save_retry_multiplier = _int_field(
        raw.get("address_save_retry_multiplier"),
        "yggdrasil_service_setup.address_save_retry_multiplier",
    )
    if address_save_retry_multiplier < 2:
        raise ConfigError(
            "yggdrasil_service_setup.address_save_retry_multiplier must be at least 2"
        )
    address_save_retry_max_seconds = _int_field(
        raw.get("address_save_retry_max_seconds"),
        "yggdrasil_service_setup.address_save_retry_max_seconds",
    )
    if address_save_retry_max_seconds < address_save_retry_base_seconds:
        raise ConfigError(
            "yggdrasil_service_setup.address_save_retry_max_seconds must be at "
            "least address_save_retry_base_seconds"
        )
    connection_wait_base_seconds = _int_field(
        raw.get("connection_wait_base_seconds"),
        "yggdrasil_service_setup.connection_wait_base_seconds",
    )
    if connection_wait_base_seconds < 1:
        raise ConfigError(
            "yggdrasil_service_setup.connection_wait_base_seconds must be positive"
        )
    connection_wait_multiplier = _int_field(
        raw.get("connection_wait_multiplier"),
        "yggdrasil_service_setup.connection_wait_multiplier",
    )
    if connection_wait_multiplier < 2:
        raise ConfigError(
            "yggdrasil_service_setup.connection_wait_multiplier must be at least 2"
        )
    connection_wait_max_seconds = _int_field(
        raw.get("connection_wait_max_seconds"),
        "yggdrasil_service_setup.connection_wait_max_seconds",
    )
    if connection_wait_max_seconds < connection_wait_base_seconds:
        raise ConfigError(
            "yggdrasil_service_setup.connection_wait_max_seconds must be at "
            "least connection_wait_base_seconds"
        )
    return YggdrasilServiceSetupConfig(
        github_repo=github_repo,
        download_dir=download_dir,
        service_unit_name=service_unit_name,
        install_retries=install_retries,
        config_path=config_path,
        private_key_path=private_key_path,
        config_file_mode=config_file_mode,
        private_key_file_mode=private_key_file_mode,
        if_name=if_name,
        if_mtu=if_mtu,
        admin_listen=admin_listen,
        listen=listen,
        multicast_interfaces=multicast_interfaces,
        peers_full_path=peers_full_path,
        peers_tarball_url=peers_tarball_url,
        peer_batch_size=peer_batch_size,
        peer_target_count=peer_target_count,
        peer_probe_timeout_seconds=peer_probe_timeout_seconds,
        peer_max_batches=peer_max_batches,
        static_peers=static_peers,
        address_file_path=address_file_path,
        address_file_mode=address_file_mode,
        address_save_retry_base_seconds=address_save_retry_base_seconds,
        address_save_retry_multiplier=address_save_retry_multiplier,
        address_save_retry_max_seconds=address_save_retry_max_seconds,
        connection_wait_base_seconds=connection_wait_base_seconds,
        connection_wait_multiplier=connection_wait_multiplier,
        connection_wait_max_seconds=connection_wait_max_seconds,
    )
