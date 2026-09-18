"""Values of the i2pd_service_setup task and of its deployed address command.

The task installs the newest i2pd release from its GitHub repository as a system
service and owns two files: the main configuration at CONFIG_PATH and the
tunnels file at TUNNELS_CONFIG_PATH, which the main configuration names through
tunconf, so i2pd reads exactly the owned file wherever the package default
points. The tunnels file publishes the SSH server tunnel of this machine, and
the port that tunnel forwards to is not a value of this module: it is the
ssh_daemon_setup Port directive, the single source of truth, so the tunnel and
the daemon can never diverge.

The deployed address command reads the values of this module directly, so the
section has no copy in the config document and no config path travels in the
command line beyond the ssh_daemon_setup read it still needs. The section
declares no boolean switch as a text: HTTP_ENABLED and SOCKS_PROXY_ENABLED are
whole numbers, and CONFIG_TRUE_VALUE with CONFIG_FALSE_VALUE is the spelling
this foreign configuration format accepts.
"""

from __future__ import annotations

from pathlib import Path

# GitHub repository of i2pd in owner/name form, as the releases API takes it.
GITHUB_REPO: str = "PurpleI2P/i2pd"

# Directory for the downloaded package file.
DOWNLOAD_DIR: Path = Path("/var/lib/pyntara/i2pd-download")

# Distribution identity file the task reads to check that the packages apply to
# this system and to name the release asset.
OS_RELEASE_FILE_PATH: Path = Path("/etc/os-release")

# Systemd service unit installed by the package.
SERVICE_UNIT_NAME: str = "i2pd.service"

# Main configuration file the task owns. It must match the --conf path of the
# package unit, otherwise the rendered values are ignored.
CONFIG_PATH: Path = Path("/etc/i2pd/i2pd.conf")

# i2pd log verbosity: debug, info, warn, error or none.
LOG_LEVEL: str = "warn"

# Total router bandwidth limit in kilobytes per second; 100 Mbit/s is
# 12500 KB/s. The limit applies to both directions combined.
BANDWIDTH: int = 12500

# Percentage of the router bandwidth shared for transit traffic: 1 means one
# percent, so the transit limit is BANDWIDTH * SHARE / 100.
SHARE: int = 1

# Switches of the rendered configuration: 1 turns the option on and 0 turns it
# off, so the web console and the SOCKS proxy are declared the same way every
# documented switch of this project is. The proxy is the way an ssh client
# reaches this machine over I2P.
HTTP_ENABLED: int = 0
SOCKS_PROXY_ENABLED: int = 1

# Port the SOCKS proxy listens on (socksproxy.port of i2pd.conf). The network
# telemetry builds the ssh command of this channel through exactly this
# address, so the port has one home and no caller guesses the i2pd default.
SOCKS_PROXY_PORT: int = 4447

# Retry attempts after a failed package install; total attempts are retries
# plus one.
INSTALL_RETRIES: int = 3

# Readiness loop of the forking service after a start or a restart: attempts
# and the pause between two is-active checks.
START_CHECK_ATTEMPTS: int = 5
START_CHECK_RETRY_DELAY_SECONDS: float = 1

# Owned tunnels configuration file with the SSH server tunnel. The main
# configuration names it through tunconf. The tunnel port is not configured
# here: it is read from the ssh_daemon_setup Port directive.
TUNNELS_CONFIG_PATH: Path = Path("/etc/i2pd/tunnels.conf")

# Section name of the SSH server tunnel in the tunnels file and the local
# address that tunnel forwards to; the SSH daemon listens on it because the
# tunnel connects from the same machine.
TUNNEL_NAME: str = "ssh"
TUNNEL_HOST: str = "127.0.0.1"

# Identity file of the tunnel destination, created by i2pd on the first start.
# The file lives in the i2pd data directory (/var/lib/i2pd), the only place the
# i2pd process may write: the AppArmor profile grants read-only access to
# /etc/i2pd, and i2pd resolves every keys path against its data directory
# anyway, so the tunnels file carries the file name only. The task computes the
# .b32.i2p address from this file, so the address survives restarts.
TUNNEL_KEYS_PATH: Path = Path("/var/lib/i2pd/ssh.dat")

# Path and mode of the saved SSH tunnel address file. The task writes the
# computed address here once the identity exists, and the deployed address
# command reads this file as the fallback when the live keys file cannot be
# decoded. The address is not a secret, so the file is readable by every user.
ADDRESS_FILE_PATH: Path = Path("/var/lib/pyntara/i2pd_ssh_address")
ADDRESS_FILE_MODE: int = 0o644

# Name templates of the two candidate .deb assets of a release, formatted with
# {release_tag}, {codename} and {arch}. The codename-specific build wins,
# because it is built against this distribution; the generic build is the
# fallback, so a release without a build for this codename still installs.
CODENAME_ASSET_NAME_TEMPLATE: str = "i2pd_{release_tag}-1{codename}1_{arch}.deb"
GENERIC_ASSET_NAME_TEMPLATE: str = "i2pd_{release_tag}-1_{arch}.deb"

# Key of the os-release file that carries the distribution codename the
# codename-specific asset name is built from.
OS_RELEASE_CODENAME_KEY: str = "VERSION_CODENAME"

# Command that prints the installed version; the task reads the first dotted
# version triple from its output. A missing binary or a nonzero exit means i2pd
# is not installed, so the task reinstalls it. The three systemctl calls carry
# the unit name as their {service_unit_name} placeholder.
VERSION_COMMAND: tuple[str, ...] = ("i2pd", "--version")
SERVICE_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)
SERVICE_START_COMMAND: tuple[str, ...] = ("systemctl", "start", "{service_unit_name}")
SERVICE_RESTART_COMMAND: tuple[str, ...] = (
    "systemctl",
    "restart",
    "{service_unit_name}",
)

# Name of the two templates under task_data/i2pd_service_setup/ of the clone:
# the main configuration and the tunnels file.
CONFIG_TEMPLATE_FILE_NAME: str = "i2pd.conf"
TUNNELS_TEMPLATE_FILE_NAME: str = "tunnels.conf"

# Boolean spelling i2pd accepts in its configuration file. The rendered file,
# the idempotency comparison and the written file share it, so one
# representation covers all three.
CONFIG_TRUE_VALUE: str = "true"
CONFIG_FALSE_VALUE: str = "false"

# Readiness loop of the tunnel identity: the keys file appears only after the
# first start of the router, so the task repeats the decode with a pause until
# these attempts run out and then reports what it found.
ADDRESS_CHECK_ATTEMPTS: int = 10
ADDRESS_CHECK_RETRY_DELAY_SECONDS: float = 2

# Name the record of this channel carries in the network report, so a reader
# sees which network the reported ssh command goes through, and the suffix of
# the address the task computes from the identity key: the base32 form followed
# by the domain of the network.
REPORT_CHANNEL_NAME: str = "i2p"
ADDRESS_SUFFIX: str = ".b32.i2p"

# The names the task and the deployed command read. The list lives next to the
# values it names, so a module that stops declaring one of them is reported by
# name instead of raising while the run is under way.
READ_VALUE_NAMES: tuple[str, ...] = (
    "GITHUB_REPO",
    "DOWNLOAD_DIR",
    "OS_RELEASE_FILE_PATH",
    "SERVICE_UNIT_NAME",
    "CONFIG_PATH",
    "LOG_LEVEL",
    "BANDWIDTH",
    "SHARE",
    "HTTP_ENABLED",
    "SOCKS_PROXY_ENABLED",
    "SOCKS_PROXY_PORT",
    "INSTALL_RETRIES",
    "START_CHECK_ATTEMPTS",
    "START_CHECK_RETRY_DELAY_SECONDS",
    "TUNNELS_CONFIG_PATH",
    "TUNNEL_NAME",
    "TUNNEL_HOST",
    "TUNNEL_KEYS_PATH",
    "ADDRESS_FILE_PATH",
    "ADDRESS_FILE_MODE",
    "CODENAME_ASSET_NAME_TEMPLATE",
    "GENERIC_ASSET_NAME_TEMPLATE",
    "OS_RELEASE_CODENAME_KEY",
    "VERSION_COMMAND",
    "SERVICE_ENABLE_COMMAND",
    "SERVICE_START_COMMAND",
    "SERVICE_RESTART_COMMAND",
    "CONFIG_TEMPLATE_FILE_NAME",
    "TUNNELS_TEMPLATE_FILE_NAME",
    "CONFIG_TRUE_VALUE",
    "CONFIG_FALSE_VALUE",
    "ADDRESS_CHECK_ATTEMPTS",
    "ADDRESS_CHECK_RETRY_DELAY_SECONDS",
    "REPORT_CHANNEL_NAME",
    "ADDRESS_SUFFIX",
)
