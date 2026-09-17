"""Values of the ssh_client_setup task.

The task patches the system-wide SSH client configuration through a drop-in,
never through ssh_config itself, which is only checked for an Include directive
that pulls the drop-in directory in. Directives are written through augeas under
the Host * block, so they apply to every connection; a directive that is already
present with the same value is left untouched, a directive with a different
value is updated, and a directive that is no longer configured is removed. The
pair of package install values comes from the shared module common, because
every task that installs a package reads the same pair.
"""

from __future__ import annotations

from pathlib import Path

from pyntara.values.common import SshDirective

# Main SSH client configuration file, never rewritten by the task: it is only
# checked for the Include directive below.
SSH_CONFIG_PATH: Path = Path("/etc/ssh/ssh_config")

# Drop-in configuration file the task owns, written through augeas under the
# Host * block.
SSH_CONFIG_DROPIN_PATH: Path = Path("/etc/ssh/ssh_config.d/pyntara.conf")

# File mode of the rendered drop-in.
DROPIN_FILE_MODE: int = 0o644

# Ownership comment of the drop-in. It is written without the leading hash,
# because augeas stores and writes comment values without it.
DROPIN_HEADER: str = "Managed by the Pyntara ssh_client_setup task."

# Sign that marks a comment in the edited files. The task leaves a line carrying
# it untouched and reads the ownership comment of the drop-in by it, so a file
# whose syntax marks comments in another way is answered here.
DROPIN_COMMENT_SIGN: str = "#"

# Name of the directive that pulls the drop-in directory into the main
# configuration. The task reads it to prove the drop-in is included, so the
# keyword of the foreign file is a value and its comparison ignores case.
INCLUDE_DIRECTIVE: str = "Include"

# Command that prints the effective SSH client configuration, used to verify
# that every configured directive is in force after the drop-in is written; the
# host is the one the probe asks about.
EFFECTIVE_CONFIG_COMMAND: tuple[str, ...] = ("ssh", "-G", "example.com")

# augeas lens for the ssh_config syntax, the container node the directives are
# placed under, and the pattern that node carries, so they apply to every
# connection.
AUGEAS_LENS: str = "Ssh.lns"
AUGEAS_CONTAINER: str = "Host"
AUGEAS_CONTAINER_VALUE: str = "*"

# Package that provides the augtool command line tool used to write the
# drop-in. The task installs it itself when the tool is missing, so it never
# waits for another task to provide augeas.
AUGEAS_TOOLS_PACKAGE_NAME: str = "augeas-tools"

# The ssh_config keywords guaranteed by the task, rendered into the drop-in
# under the Host * block in this order. A keyword in this list that the client
# no longer knows makes the ssh -G verification report it, so a dropped keyword
# is removed here and nowhere else.
DIRECTIVES: tuple[SshDirective, ...] = (
    SshDirective("AddressFamily", "any"),
    SshDirective("CheckHostIP", "no"),
    SshDirective("Compression", "yes"),
    SshDirective("ConnectionAttempts", "17"),
    SshDirective("ConnectTimeout", "31"),
    SshDirective("NumberOfPasswordPrompts", "5"),
    SshDirective("PasswordAuthentication", "yes"),
    SshDirective("TCPKeepAlive", "yes"),
    SshDirective("ServerAliveInterval", "61"),
    SshDirective("ServerAliveCountMax", "17"),
    SshDirective("PreferredAuthentications", "publickey,password"),
    SshDirective("StrictHostKeyChecking", "accept-new"),
)

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error. The pair of package install values comes from
# the shared module common.
READ_VALUE_NAMES: tuple[str, ...] = (
    "SSH_CONFIG_PATH",
    "SSH_CONFIG_DROPIN_PATH",
    "DROPIN_FILE_MODE",
    "DROPIN_HEADER",
    "DROPIN_COMMENT_SIGN",
    "INCLUDE_DIRECTIVE",
    "EFFECTIVE_CONFIG_COMMAND",
    "AUGEAS_LENS",
    "AUGEAS_CONTAINER",
    "AUGEAS_CONTAINER_VALUE",
    "AUGEAS_TOOLS_PACKAGE_NAME",
    "DIRECTIVES",
)
