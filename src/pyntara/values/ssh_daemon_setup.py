"""Values of the ssh_daemon_setup task.

The task installs the openssh-server package, runs ssh.service and patches the
daemon configuration through a drop-in, never through sshd_config itself.
Directives are written through augeas (augtool from the package named here,
which the task installs itself when the tool is missing), which parses the real
syntax and updates only what differs, so the file is never blindly overwritten.

The port of the daemon is carried by the Port entry of DIRECTIVES and is read
through pyntara.ssh by every task and command that forwards to the daemon, so
the forward target exists once and no caller keeps a second copy. The
pre-generated key pair lives in task_data/ssh_daemon_setup/: the private key is
encrypted with a strong pass phrase, so it is committed to the repository as it
stands, and the task copies both files to root and to every user of USERS and
guarantees the public key in authorized_keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SshDirective:
    """One sshd_config directive: a keyword and its value.

    The value is kept as a single string and joined as-is into the rendered
    drop-in, so the directive spelling stays exactly as declared.
    """

    name: str
    value: str


# Package that provides the SSH server daemon, the package that provides the
# augtool command line tool used to write the drop-in, and the seconds the dpkg
# status query may take. The task installs the augeas package itself when the
# tool is missing, so it never waits for another task to provide it.
PACKAGE_NAME: str = "openssh-server"
AUGEAS_TOOLS_PACKAGE_NAME: str = "augeas-tools"
PACKAGE_STATUS_TIMEOUT_SECONDS: int = 30

# Retry attempts after a failed package install; total attempts are retries
# plus one.
INSTALL_RETRIES: int = 3

# Systemd units of the daemon. Ubuntu activates the daemon through the socket,
# and the socket then owns the listen port: the Port directive of sshd_config
# is ignored while the socket is enabled. The task disables the socket, so the
# daemon listens on the port from the configuration, written through augeas
# into the drop-in.
SERVICE_UNIT_NAME: str = "ssh.service"
SOCKET_UNIT_NAME: str = "ssh.socket"

# Readiness loop after a start: attempts and the pause between two is-active
# checks. The reload path never waits, because the daemon stays up.
START_CHECK_ATTEMPTS: int = 5
START_CHECK_RETRY_DELAY_SECONDS: float = 1

# Main sshd configuration file, which is only checked for an Include directive
# that pulls the drop-in directory in and never rewritten, and the drop-in the
# task owns. Directives are written through augeas and updated only when they
# differ; an empty directive list removes the drop-in file.
SSHD_CONFIG_PATH: Path = Path("/etc/ssh/sshd_config")
SSHD_CONFIG_DROPIN_PATH: Path = Path("/etc/ssh/sshd_config.d/pyntara.conf")

# File mode of the rendered drop-in, the sign that marks a comment in the
# edited files, the ownership comment written at the top of the drop-in and the
# directive that pulls the drop-in directory into the main configuration. The
# header is written without its leading sign, because augeas stores and writes
# comment values without it; the task reads the comment by the sign, and a file
# whose syntax marks comments another way is answered here. The keyword of the
# foreign file is a value, so its comparison ignores case.
DROPIN_FILE_MODE: int = 0o644
DROPIN_COMMENT_SIGN: str = "#"
DROPIN_HEADER: str = "Managed by the Pyntara ssh_daemon_setup task."
INCLUDE_DIRECTIVE: str = "Include"

# Augeas lens of the sshd_config syntax, and the name of the directive that
# carries the listen port: the task compares that directive through the drop-in
# to decide whether a change needs a restart or only a reload, and pyntara.ssh
# reads it as the forward target port of every tunnel of this machine.
AUGEAS_LENS: str = "Sshd.lns"
PORT_DIRECTIVE: str = "Port"

# Repository key file names under task_data/ssh_daemon_setup/. The deployed name
# equals the repository name, and the pair is named like the OpenSSH default
# identity id_ed25519, so the client offers it automatically without -i or
# ssh-add.
PRIVATE_KEY_FILE_NAME: str = "id_ed25519"
PUBLIC_KEY_FILE_NAME: str = "id_ed25519.pub"

# Repository file names of the port-forwarding key pair, deployed in parallel
# with the main pair to the same .ssh directories. The key is dedicated to
# reverse ssh tunnels only: its private key stays passphrase-protected, and its
# public key line in authorized_keys carries the restriction prefix below,
# which disables every capability, re-enables only port forwarding and allows
# any listen port on the server side. Consumed by the auto_port_forwarding
# service, which unlocks the private key with the vault passphrase.
PORT_FORWARDING_PRIVATE_KEY_FILE_NAME: str = "id_ed25519_pf"
PORT_FORWARDING_PUBLIC_KEY_FILE_NAME: str = "id_ed25519_pf.pub"
PORT_FORWARDING_AUTHORIZED_KEYS_OPTIONS: str = (
    'restrict,port-forwarding,permitlisten="*"'
)

# File modes of the deployed key files: the private key stays readable only by
# its owner, the public key is world-readable. The authorized_keys file is
# private, and the .ssh directories created for the users are closed to
# everyone else.
PRIVATE_KEY_FILE_MODE: int = 0o600
PUBLIC_KEY_FILE_MODE: int = 0o644
AUTHORIZED_KEYS_FILE_MODE: int = 0o600
SSH_DIR_MODE: int = 0o700

# Target .ssh directory of the root user, and the additional users whose .ssh
# directories receive the keys. A user that does not exist yet is skipped with
# a log line, so the task stays idempotent.
ROOT_SSH_DIR: Path = Path("/root/.ssh")
USERS: tuple[str, ...] = ("i", "j", "k")

# Commands the task runs: the effective configuration of the daemon, the
# listener table, and the systemctl calls on the two units. The unit is named
# by SOCKET_UNIT_NAME or SERVICE_UNIT_NAME at the call site.
EFFECTIVE_CONFIG_COMMAND: tuple[str, ...] = ("sshd", "-T")
LISTENING_SOCKETS_COMMAND: tuple[str, ...] = ("ss", "-tlnp")
SOCKET_DISABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "disable",
    "--now",
    "{socket_unit_name}",
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
SERVICE_RELOAD_COMMAND: tuple[str, ...] = (
    "systemctl",
    "reload",
    "{service_unit_name}",
)

# sshd_config directives guaranteed by the task, written through augeas into the
# drop-in. A directive that is already present with the same value is left
# untouched, one with a different value is updated, and one that is no longer
# declared is removed. Durations are whole seconds, so the sshd -T comparison
# stays exact. The window of ClientAliveInterval and ClientAliveCountMax decides
# how long the port of a reverse tunnel stays bound here after the tunnel died
# with its path, so a shorter window makes the port reusable sooner; the
# interval stays well above the tens of seconds a poor link may need to answer
# and still leaves three probes of room, which is about three minutes in total
# (docs/spec/port-forwarding-setup.md).
DIRECTIVES: tuple[SshDirective, ...] = (
    SshDirective("Port", "30222"),
    SshDirective("PubkeyAuthentication", "yes"),
    SshDirective("PermitRootLogin", "prohibit-password"),
    SshDirective("PasswordAuthentication", "no"),
    SshDirective("X11Forwarding", "yes"),
    SshDirective("UseDNS", "no"),
    SshDirective("PermitTunnel", "yes"),
    SshDirective("MaxStartups", "11:30:151"),
    SshDirective("LoginGraceTime", "360"),
    SshDirective("GatewayPorts", "yes"),
    SshDirective("Compression", "yes"),
    SshDirective("ClientAliveInterval", "60"),
    SshDirective("ClientAliveCountMax", "3"),
    SshDirective("AllowTcpForwarding", "yes"),
    SshDirective("AddressFamily", "any"),
)

# The names the task and the shared port reader read. The list lives next to the
# values it names, so a module that stops declaring one of them is reported by
# name instead of raising while the run is under way.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGE_NAME",
    "AUGEAS_TOOLS_PACKAGE_NAME",
    "PACKAGE_STATUS_TIMEOUT_SECONDS",
    "INSTALL_RETRIES",
    "SERVICE_UNIT_NAME",
    "SOCKET_UNIT_NAME",
    "START_CHECK_ATTEMPTS",
    "START_CHECK_RETRY_DELAY_SECONDS",
    "SSHD_CONFIG_PATH",
    "SSHD_CONFIG_DROPIN_PATH",
    "DROPIN_FILE_MODE",
    "DROPIN_COMMENT_SIGN",
    "DROPIN_HEADER",
    "INCLUDE_DIRECTIVE",
    "AUGEAS_LENS",
    "PORT_DIRECTIVE",
    "PRIVATE_KEY_FILE_NAME",
    "PUBLIC_KEY_FILE_NAME",
    "PORT_FORWARDING_PRIVATE_KEY_FILE_NAME",
    "PORT_FORWARDING_PUBLIC_KEY_FILE_NAME",
    "PORT_FORWARDING_AUTHORIZED_KEYS_OPTIONS",
    "PRIVATE_KEY_FILE_MODE",
    "PUBLIC_KEY_FILE_MODE",
    "AUTHORIZED_KEYS_FILE_MODE",
    "SSH_DIR_MODE",
    "ROOT_SSH_DIR",
    "USERS",
    "EFFECTIVE_CONFIG_COMMAND",
    "LISTENING_SOCKETS_COMMAND",
    "SOCKET_DISABLE_COMMAND",
    "SERVICE_ENABLE_COMMAND",
    "SERVICE_START_COMMAND",
    "SERVICE_RESTART_COMMAND",
    "SERVICE_RELOAD_COMMAND",
    "DIRECTIVES",
)
