"""Values of the yggdrasil_service_setup task and of its deployed address command.

The task installs the newest yggdrasil release from its GitHub repository as a
system service and owns the configuration document and the node key: the key
lives in a separate PEM file referenced by PrivateKeyPath, so a configuration
rewrite never changes the node identity. The peers come from the official
public-peers repository, are probed in batches through the admin socket, and only
the working ones go into the rendered configuration; STATIC_PEERS is the fallback
for a machine that cannot download the list.

The deployed address command reads the values of this module directly, so the
section has no copy in a second place. The schema of the yggdrasil
configuration and of the admin socket answer belongs to the program, and the key
names are visible here instead of hidden in the task module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MulticastInterface:
    """One MulticastInterfaces block of the configuration document.

    regex selects the interfaces, beacon advertises the presence of this node
    and listen connects to the neighbours found that way.
    """

    regex: str
    beacon: bool
    listen: bool


# GitHub repository of yggdrasil in owner/name form, as the releases API takes
# it, and the directory of the downloaded package.
GITHUB_REPO: str = "yggdrasil-network/yggdrasil-go"
DOWNLOAD_DIR: Path = Path("/var/lib/pyntara/yggdrasil-download")

# Systemd service unit installed by the package, and the retry attempts after a
# failed package install; total attempts are retries plus one.
SERVICE_UNIT_NAME: str = "yggdrasil.service"
INSTALL_RETRIES: int = 3

# Main configuration file the task writes, which must match the
# DefaultConfigFile embedded in the package, and the PEM file with the node
# private key referenced by PrivateKeyPath. The task extracts the key from the
# package-generated configuration once, so the node identity survives
# configuration rewrites. The configuration is readable by its group and the key
# only by its owner.
CONFIG_PATH: Path = Path("/etc/yggdrasil/yggdrasil.conf")
PRIVATE_KEY_PATH: Path = Path("/etc/yggdrasil/private-key.pem")
CONFIG_FILE_MODE: int = 0o640
PRIVATE_KEY_FILE_MODE: int = 0o600

# NetworkManager drop-in that marks the yggdrasil interface as unmanaged, so
# NetworkManager never assumes it as an external device and no other task can
# persist an ephemeral profile for it, with the file mode of that drop-in and
# the directory of the netplan YAML files that back NetworkManager connections;
# a leftover connection for the interface is moved aside there as a .bak.
NM_UNMANAGED_CONF_PATH: Path = Path(
    "/etc/NetworkManager/conf.d/yggdrasil-unmanaged.conf"
)
NM_UNMANAGED_CONF_FILE_MODE: int = 0o644
NETPLAN_DIR_PATH: Path = Path("/etc/netplan")

# TUN interface name, fixed instead of automatic, and its MTU; the yggdrasil
# range is 1280 to 65535.
IF_NAME: str = "ygg"
IF_MTU: int = 65535

# Admin socket URI used by yggdrasilctl, and the inbound listener URIs. The
# wildcard address binds IPv4 and IPv6 and port 0 picks a random free port;
# wss, socks and sockstls are not supported as listeners.
ADMIN_LISTEN: str = "unix:///var/run/yggdrasil/yggdrasil.sock"
LISTEN: tuple[str, ...] = (
    "tcp://[::]:0",
    "tls://[::]:0",
    "quic://[::]:0",
    "ws://[::]:0",
)

# Multicast peer discovery: one block matching every interface with both
# switches covers the local network.
MULTICAST_INTERFACES: tuple[MulticastInterface, ...] = (
    MulticastInterface(regex=".*", beacon=True, listen=True),
)

# Peer list: the full downloaded list is saved next to the configuration for
# reference, while only the selected working peers go into it. The tarball comes
# from the official public-peers repository, PEER_BATCH_SIZE peers are probed at
# once, PEER_TARGET_COUNT working ones are kept, PEER_PROBE_TIMEOUT_SECONDS
# bounds the wait for one batch to connect and PEER_MAX_BATCHES bounds the walk
# over the list, where 0 means the whole list. The connections arrive in a wave
# some 30 to 35 seconds after the restart, because the dials run with a bounded
# parallelism and the dead peers eat their timeout, so 60 seconds leaves a safe
# margin. STATIC_PEERS is the fallback used when the download fails: pick two or
# three nearby nodes from the public peer lists.
PEERS_FULL_PATH: Path = Path("/etc/yggdrasil/peers-full.txt")
PEERS_TARBALL_URL: str = (
    "https://codeload.github.com/yggdrasil-network/public-peers/tar.gz/refs/heads/master"
)
PEER_BATCH_SIZE: int = 100
PEER_TARGET_COUNT: int = 11
PEER_PROBE_TIMEOUT_SECONDS: float = 60
PEER_MAX_BATCHES: int = 0
STATIC_PEERS: tuple[str, ...] = ()

# Path and mode of the saved node self address file. The task writes the address
# reported by the admin socket here once the node is provisioned, and the
# deployed address command reads it as the fallback when the live query fails.
# The address is not secret, so the file is readable by every user.
ADDRESS_FILE_PATH: Path = Path("/var/lib/pyntara/yggdrasil_self_address")
ADDRESS_FILE_MODE: int = 0o644

# Retry of the self address save after the final restart: the admin socket is
# not ready immediately, so the getSelf query is repeated with a geometric
# backoff while the total budget lasts. The first wait is the base in seconds,
# every further failure multiplies the pause by the multiplier until the budget
# in seconds is spent.
ADDRESS_SAVE_RETRY_BASE_SECONDS: int = 1
ADDRESS_SAVE_RETRY_MULTIPLIER: int = 2
ADDRESS_SAVE_RETRY_MAX_SECONDS: int = 67

# Retry of the live connection check after the final restart, mirroring the
# address save: the peers need a moment to re-establish their connections.
CONNECTION_WAIT_BASE_SECONDS: int = 1
CONNECTION_WAIT_MULTIPLIER: int = 2
CONNECTION_WAIT_MAX_SECONDS: int = 30

# Name the record of this channel carries in the network report, so a reader
# sees which network the reported ssh command goes through.
REPORT_CHANNEL_NAME: str = "yggdrasil"

# Name template of the release asset the task installs, where {version} is the
# release tag without RELEASE_TAG_PREFIX and {arch} the dpkg architecture.
ASSET_NAME_TEMPLATE: str = "yggdrasil-{version}-{arch}.deb"
RELEASE_TAG_PREFIX: str = "v"

# Commands the task runs, and the admin socket calls the task and the deployed
# address command share. {config_path} is CONFIG_PATH and {service_unit_name}
# the unit above; the journal query reads one probe window of the last
# {probe_seconds} seconds in a timestamp format a machine can compare. The -json
# flag is part of the admin calls, because the shared parser reads JSON and the
# socket prints a table without it.
INSTALLED_VERSION_COMMAND: tuple[str, ...] = ("yggdrasil", "-version")
EXPORT_KEY_FROM_CONFIG_COMMAND: tuple[str, ...] = (
    "yggdrasil",
    "-useconffile",
    "{config_path}",
    "-exportkey",
)
GENERATE_CONFIG_COMMAND: tuple[str, ...] = ("yggdrasil", "-genconf", "-json")
EXPORT_KEY_FROM_STDIN_COMMAND: tuple[str, ...] = ("yggdrasil", "-useconf", "-exportkey")
PEERS_LATENCY_COMMAND: tuple[str, ...] = ("yggdrasilctl", "-json", "getPeers")
SELF_ADDRESS_COMMAND: tuple[str, ...] = ("yggdrasilctl", "-json", "getSelf")
JOURNAL_CONNECTED_QUERY_COMMAND: tuple[str, ...] = (
    "journalctl",
    "-u",
    "{service_unit_name}",
    "--since",
    "-{probe_seconds}s",
    "--no-pager",
    "--output=short-iso",
)
SERVICE_START_COMMAND: tuple[str, ...] = ("systemctl", "start", "{service_unit_name}")
SERVICE_RESTART_COMMAND: tuple[str, ...] = (
    "systemctl",
    "restart",
    "{service_unit_name}",
)
SERVICE_ENABLE_COMMAND: tuple[str, ...] = ("systemctl", "enable", "{service_unit_name}")

# NetworkManager and iproute2 calls of the unmanaged rule and of the leftover
# interface cleanup, each carrying the name it acts on.
NMCLI_RELOAD_COMMAND: tuple[str, ...] = ("nmcli", "general", "reload")
NMCLI_CONNECTION_SHOW_COMMAND: tuple[str, ...] = (
    "nmcli",
    "connection",
    "show",
    "{connection_name}",
)
NMCLI_CONNECTION_DELETE_COMMAND: tuple[str, ...] = (
    "nmcli",
    "connection",
    "delete",
    "{connection_name}",
)
IP_LINK_SHOW_COMMAND: tuple[str, ...] = ("ip", "link", "show", "dev", "{interface_name}")
IP_LINK_DELETE_COMMAND: tuple[str, ...] = ("ip", "link", "del", "{interface_name}")

# Body of the NetworkManager drop-in, the line a netplan YAML carries for the
# yggdrasil connection, the suffix of the netplan files the task inspects and the
# suffix a profile is moved aside with, so netplan stops reading it and cannot
# regenerate the deleted connection at the next boot. {interface_name} is
# IF_NAME.
NM_UNMANAGED_CONF_BODY: str = (
    "[keyfile]\nunmanaged-devices=interface-name:{interface_name}\n"
)
NETPLAN_INTERFACE_MARKER: str = 'connection.interface-name: "{interface_name}"'
NETPLAN_FILE_SUFFIX: str = ".yaml"
NETPLAN_BACKUP_SUFFIX: str = ".bak"

# Prefix and suffix of the temporary file the peer tarball is downloaded into
# before it is unpacked, and the suffix of the markdown files of the
# public-peers repository that carry the peer URIs.
PEERS_TARBALL_TEMP_PREFIX: str = "yggdrasil-peers-"
PEERS_TARBALL_TEMP_SUFFIX: str = ".tar.gz"
PEER_MARKDOWN_SUFFIX: str = ".md"

# Separator of the lines of the text files the task writes, so the saved peer
# list and the saved address file carry one line each and no file ends without a
# final separator, and the indentation of the JSON document it renders, shared
# by the rendered file and the comparison that decides whether it must be
# rewritten.
LINE_SEPARATOR: str = "\n"
CONFIG_JSON_INDENT: int = 2

# Key names of the yggdrasil configuration document the task renders and reads
# back, and field names of the JSON document the admin socket answers with,
# shared by the task and the deployed address command. The schema belongs to
# yggdrasil, so it is visible here instead of hidden in the task module.
CONFIG_DOCUMENT_KEYS: dict[str, str] = {
    "private_key_path": "PrivateKeyPath",
    "admin_listen": "AdminListen",
    "if_name": "IfName",
    "if_mtu": "IfMTU",
    "listen": "Listen",
    "multicast_interfaces": "MulticastInterfaces",
    "multicast_regex": "Regex",
    "multicast_beacon": "Beacon",
    "multicast_listen": "Listen",
    "peers": "Peers",
}
ADMIN_OUTPUT_KEYS: dict[str, str] = {
    "address": "address",
    "peers": "peers",
    "remote": "remote",
    "latency": "latency",
}

# The names the task and the deployed command read. The list lives next to the
# values it names, so a module that stops declaring one of them is reported by
# name instead of raising while the run is under way.
READ_VALUE_NAMES: tuple[str, ...] = (
    "GITHUB_REPO",
    "DOWNLOAD_DIR",
    "SERVICE_UNIT_NAME",
    "INSTALL_RETRIES",
    "CONFIG_PATH",
    "PRIVATE_KEY_PATH",
    "CONFIG_FILE_MODE",
    "PRIVATE_KEY_FILE_MODE",
    "NM_UNMANAGED_CONF_PATH",
    "NM_UNMANAGED_CONF_FILE_MODE",
    "NETPLAN_DIR_PATH",
    "IF_NAME",
    "IF_MTU",
    "ADMIN_LISTEN",
    "LISTEN",
    "MULTICAST_INTERFACES",
    "PEERS_FULL_PATH",
    "PEERS_TARBALL_URL",
    "PEER_BATCH_SIZE",
    "PEER_TARGET_COUNT",
    "PEER_PROBE_TIMEOUT_SECONDS",
    "PEER_MAX_BATCHES",
    "STATIC_PEERS",
    "ADDRESS_FILE_PATH",
    "ADDRESS_FILE_MODE",
    "ADDRESS_SAVE_RETRY_BASE_SECONDS",
    "ADDRESS_SAVE_RETRY_MULTIPLIER",
    "ADDRESS_SAVE_RETRY_MAX_SECONDS",
    "CONNECTION_WAIT_BASE_SECONDS",
    "CONNECTION_WAIT_MULTIPLIER",
    "CONNECTION_WAIT_MAX_SECONDS",
    "REPORT_CHANNEL_NAME",
    "ASSET_NAME_TEMPLATE",
    "RELEASE_TAG_PREFIX",
    "INSTALLED_VERSION_COMMAND",
    "EXPORT_KEY_FROM_CONFIG_COMMAND",
    "GENERATE_CONFIG_COMMAND",
    "EXPORT_KEY_FROM_STDIN_COMMAND",
    "PEERS_LATENCY_COMMAND",
    "SELF_ADDRESS_COMMAND",
    "JOURNAL_CONNECTED_QUERY_COMMAND",
    "SERVICE_START_COMMAND",
    "SERVICE_RESTART_COMMAND",
    "SERVICE_ENABLE_COMMAND",
    "NMCLI_RELOAD_COMMAND",
    "NMCLI_CONNECTION_SHOW_COMMAND",
    "NMCLI_CONNECTION_DELETE_COMMAND",
    "IP_LINK_SHOW_COMMAND",
    "IP_LINK_DELETE_COMMAND",
    "NM_UNMANAGED_CONF_BODY",
    "NETPLAN_INTERFACE_MARKER",
    "NETPLAN_FILE_SUFFIX",
    "NETPLAN_BACKUP_SUFFIX",
    "PEERS_TARBALL_TEMP_PREFIX",
    "PEERS_TARBALL_TEMP_SUFFIX",
    "PEER_MARKDOWN_SUFFIX",
    "LINE_SEPARATOR",
    "CONFIG_JSON_INDENT",
    "CONFIG_DOCUMENT_KEYS",
    "ADMIN_OUTPUT_KEYS",
)
