"""Values of the tor_setup task and of its deployed address command.

The task installs Tor from the Ubuntu archive, writes its owned settings into a
drop-in file and publishes an SSH onion service, so the machine becomes
reachable over Tor without a manual step. The local SSH port is not a value of
this module: it is the ssh_daemon_setup Port directive, the single source of
truth, so the forward of the onion service and the SSH daemon can never
diverge. The virtual port clients connect to is declared here and matches the
sshd listen port and the i2pd SSH tunnel, so the same number reaches SSH on
every path.

The deployed address command reads the values of this module directly, so the
section has no copy in a second place and the command needs no argument at
all. The include line of the main configuration is built from the drop-in path
of this module, so no second value can point the directive at another file.
"""

from __future__ import annotations

from pathlib import Path

# Package that provides the Tor daemon, and the systemd unit the task manages.
# The Ubuntu package uses the multi-instance design: the daemon runs in the
# instance unit tor@default.service, while tor.service is an empty master unit
# that always reports active.
PACKAGE_NAME: str = "tor"
SERVICE_UNIT_NAME: str = "tor@default.service"

# Main Tor configuration file, which the task never rewrites: it only
# guarantees the include line through the shared add_line_to_file helper, so
# unrelated content of the file survives.
TORRC_PATH: Path = Path("/etc/tor/torrc")

# Owned drop-in file with the task settings, connected to the main
# configuration through the include line built from this path. The file lives
# directly in /etc/tor: the AppArmor profile of the package allows reading
# /etc/tor/* but not its subdirectories, and a plain file path avoids the
# directory listing a glob would need. The task rewrites this file whenever the
# rendered content differs, so manual edits are reverted on the next run.
TORRC_DROPIN_PATH: Path = Path("/etc/tor/pyntara.conf")

# Directive that pulls the drop-in into the main configuration, and the sign
# that marks a comment in that file. The task leaves a line carrying the sign
# untouched when it appends the directive, so a directive the operator
# commented out stays commented.
INCLUDE_DIRECTIVE: str = "%include"
TORRC_COMMENT_SIGN: str = "#"

# File mode of the drop-in, as a whole number in chmod range. The Tor daemon
# drops privileges to its own user after startup, so the file must be readable
# by it; the content is not secret.
DROPIN_FILE_MODE: int = 0o644

# Directory of the SSH onion service identity, with its mode and its system
# user. The directory must live inside /var/lib/tor, because the AppArmor
# profile of the package confines Tor to its data directory, and must be owned
# by TOR_USER, so Tor can write the keys and the hostname file; the identity
# inside is never recreated, otherwise the onion address changes. Tor refuses
# to serve an onion service from a world-readable directory, hence the 0700.
HIDDEN_SERVICE_DIR: Path = Path("/var/lib/tor/ssh")
HIDDEN_SERVICE_DIR_MODE: int = 0o700
TOR_USER: str = "debian-tor"

# Port of the SOCKS proxy bound to the loopback interface; a client routes its
# SSH connection through it to reach the onion address.
SOCKS_PORT: int = 9050

# Virtual port of the onion service: the port a client connects to on the
# .onion address, never a default a client may omit, so a client always passes
# it explicitly with -p. The value matches the sshd listen port and the i2pd
# SSH tunnel, so the same port number reaches SSH on every path.
ONION_SSH_PORT: int = 30222

# Number of introduction points of the onion service; more points keep the
# service reachable while some of them are under attack, at the cost of more
# keepalive traffic.
NUM_INTRODUCTION_POINTS: int = 6

# Tor log verbosity: debug, info, notice, warn or err.
LOG_LEVEL: str = "notice"

# Retry attempts after a failed package install; total attempts are retries
# plus one.
INSTALL_RETRIES: int = 3

# Readiness loop of the forking service after a start or a restart: attempts
# and the pause between two is-active checks.
START_CHECK_ATTEMPTS: int = 5
START_CHECK_RETRY_DELAY_SECONDS: float = 1

# Path and mode of the saved onion address file. The task writes the address
# from the hidden service hostname file here once it exists, and the deployed
# address command reads this file as the fallback when the live hostname file
# cannot be read. The address is not secret, so the file is readable by every
# user.
ADDRESS_FILE_PATH: Path = Path("/var/lib/pyntara/tor_ssh_address")
ADDRESS_FILE_MODE: int = 0o644

# Name of the drop-in template under task_data/tor_setup/ of the clone, and the
# name of the file Tor writes its onion hostname into inside the hidden service
# directory; the task reads it to report and save the address.
DROPIN_TEMPLATE_FILE_NAME: str = "torrc.conf"
HOSTNAME_FILE_NAME: str = "hostname"

# Commands the task runs. The verify call parses the whole configuration, the
# main file and every included file, as the Tor system user, because Tor
# validates the ownership of every hidden service directory against the
# process user. The three systemctl calls carry the unit name as their
# {service_unit_name} placeholder.
VERIFY_CONFIG_COMMAND: tuple[str, ...] = (
    "runuser",
    "-u",
    "{tor_user}",
    "--",
    "tor",
    "--verify-config",
)
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

# Name the record of this channel carries in the network report, so a reader
# sees which network the reported ssh command goes through.
REPORT_CHANNEL_NAME: str = "tor"

# The names the task and the deployed command read. The list lives next to the
# values it names, so a module that stops declaring one of them is reported by
# name instead of raising while the run is under way.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGE_NAME",
    "SERVICE_UNIT_NAME",
    "TORRC_PATH",
    "TORRC_DROPIN_PATH",
    "INCLUDE_DIRECTIVE",
    "TORRC_COMMENT_SIGN",
    "DROPIN_FILE_MODE",
    "HIDDEN_SERVICE_DIR",
    "HIDDEN_SERVICE_DIR_MODE",
    "TOR_USER",
    "SOCKS_PORT",
    "ONION_SSH_PORT",
    "NUM_INTRODUCTION_POINTS",
    "LOG_LEVEL",
    "INSTALL_RETRIES",
    "START_CHECK_ATTEMPTS",
    "START_CHECK_RETRY_DELAY_SECONDS",
    "ADDRESS_FILE_PATH",
    "ADDRESS_FILE_MODE",
    "DROPIN_TEMPLATE_FILE_NAME",
    "HOSTNAME_FILE_NAME",
    "VERIFY_CONFIG_COMMAND",
    "SERVICE_ENABLE_COMMAND",
    "SERVICE_START_COMMAND",
    "SERVICE_RESTART_COMMAND",
    "REPORT_CHANNEL_NAME",
)
