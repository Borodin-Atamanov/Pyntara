"""Values of the port_forwarding_setup task and of its deployed service.

The task deploys the auto_port_forwarding service that, at system start, opens
the runtime vault, reads the port-forwarding server addresses and the passphrase
of the port-forwarding key, unlocks the key in a dedicated ssh-agent and keeps a
reverse ssh tunnel to every server, forwarding the local SSH daemon port. The
forwarded local port itself is not a value of this module: it is the
ssh_daemon_setup Port directive, the single source of truth. The key pair lives
in task_data/ssh_daemon_setup/ and is deployed by the ssh_daemon_setup task,
which also guarantees the restricted public key line in authorized_keys.

The deployed service and the address command of the network report read the
values of this module directly, so the section has no copy in a second place
and no config path travels in the unit command line. The port range is
read by pyntara.forwarding_ports as well, because the router port forwarding
service of the upnp_forwarding_setup task derives the same number from the same
range, so one machine asks for one predictable port in both schemes.
"""

from __future__ import annotations

from pathlib import Path

# Title of the vault subgroup that carries the port-forwarding server
# addresses, one entry per server with the address in the url field. The
# address may be ipv4, ipv6 or a url. The group is data: the regeneration
# tooling creates it and fills it with its seed entries on creation, then never
# edits them.
VAULT_GROUP_TITLE: str = "port_forwarding_servers"

# Title of the vault entry that carries the passphrase of the deployed
# port-forwarding private key. The value must name an entry of the
# [vault_structure] values. The entry exists in every vault with a freshly
# generated passphrase per vault; the default vault's passphrase unlocks no
# deployed key.
PASSPHRASE_ENTRY_TITLE: str = "ssh_passphase_for_port_forwarding"

# User the service connects as on every port-forwarding server.
REMOTE_SSH_USER: str = "i"

# Lower and upper bound of the machine's deterministic port chain, derived as a
# function of the machine hostname. Every candidate of the chain is a port
# inside these bounds, and the size of the range is also the bound of a walk:
# once every port of the range has been offered, the next candidate could only
# repeat one of them, so the walk ends and the service pauses before starting it
# anew. The values are the Linux kernel ephemeral port range, so a candidate
# that is free on the server falls in the same zone the kernel assigns random
# ports from.
DESIRED_PORT_MIN: int = 32768
DESIRED_PORT_MAX: int = 60999

# ssh keepalive: seconds between alive probes and the number of missed probes
# before the connection is considered dead.
SERVER_ALIVE_INTERVAL_SECONDS: int = 61
SERVER_ALIVE_COUNT_MAX: int = 3

# File modes of the files the service writes: the askpass helper must be
# executable by its owner only, the state file private.
ASKPASS_HELPER_FILE_MODE: int = 0o700
STATE_FILE_MODE: int = 0o600

# Seconds a single ssh connection attempt may take before it is given up.
CONNECT_TIMEOUT_SECONDS: int = 31

# Seconds the ip call that lists this machine's own addresses may take: the
# addresses decide which vault server is this machine itself.
OWN_ADDRESSES_TIMEOUT_SECONDS: int = 15

# Seconds the ssh-agent start and the unlock of the port-forwarding key may
# take; the unlock runs ssh-add with the passphrase fed through the askpass
# helper.
AGENT_START_TIMEOUT_SECONDS: int = 15
KEY_UNLOCK_TIMEOUT_SECONDS: int = 30

# Check that the passphrase of the vault decrypts the port-forwarding key, run
# before the unlock: ssh-keygen prints the public key of a private key whose
# passphrase is right and reports an incorrect passphrase in a moment. The check
# does not use ssh-add, because a wrong passphrase there does not fail fast:
# ssh-add keeps asking the askpass helper until the caller gives up, which cost
# about thirty seconds of processor time per attempt in an endless systemd
# restart loop (measured 2026-09-25 on a machine provisioned from the default
# vault, whose passphrase unlocks no deployed key: 67 restarts of the unit). The
# command carries the passphrase in its argument list, so every caller runs it
# with command logging switched off.
KEY_CHECK_COMMAND: tuple[str, ...] = (
    "ssh-keygen",
    "-y",
    "-P",
    "{passphrase}",
    "-f",
    "{key_path}",
)
KEY_CHECK_TIMEOUT_SECONDS: int = 10

# Display the askpass helper of the key unlock is given. ssh-add needs a display
# to run the helper at all, even when the helper answers without a dialog; the
# value is the display name of the machine's desktop session.
ASKPASS_DISPLAY: str = ":0"

# Reconnect backoff after a dropped connection: the pause after the first drop
# is BACKOFF_BASE_SECONDS, every further consecutive drop multiplies the pause by
# BACKOFF_MULTIPLIER until BACKOFF_MAX_SECONDS, all whole seconds.
BACKOFF_BASE_SECONDS: int = 2
BACKOFF_MULTIPLIER: int = 2
BACKOFF_MAX_SECONDS: int = 1024

# Path of the root-only JSON file that records the assigned remote ports per
# server, so a service restart keeps the ports stable and the System Metrics
# collector reads them into the network report.
STATE_FILE_PATH: Path = Path("/var/lib/pyntara/port_forwarding_state.json")

# Name of the deployed service unit file.
SERVICE_UNIT_NAME: str = "auto_port_forwarding.service"

# Seconds systemd waits before restarting the service after a failure.
SERVICE_RESTART_SECONDS: int = 30

# Journal identifier of the service.
JOURNAL_IDENTIFIER: str = "auto_port_forwarding"

# Name of the unit template under task_data/port_forwarding_setup/ of the clone;
# the run fills its ExecStart, journal and restart placeholders.
SERVICE_TEMPLATE_FILE_NAME: str = "auto_port_forwarding.service"

# Python module the deployed unit runs from the shared deployment venv.
SERVICE_MODULE_NAME: str = "pyntara.port_forwarding"

# Command the deployed unit runs: the interpreter of the deployed venv with the
# module above. The deployed code takes no argument, because every value it
# needs ships with the values package it imports.
MODULE_RUN_COMMAND: tuple[str, ...] = ("{python}", "-m", "{module}")

# The systemctl calls of the task, each carrying the unit name as its
# {service_unit_name} placeholder except the daemon reload.
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{service_unit_name}",
)
SYSTEMCTL_RESTART_COMMAND: tuple[str, ...] = (
    "systemctl",
    "restart",
    "{service_unit_name}",
)
SYSTEMCTL_IS_FAILED_COMMAND: tuple[str, ...] = (
    "systemctl",
    "is-failed",
    "{service_unit_name}",
)

# Query of the result of the last run of the unit. A service that exited nonzero
# and waits for the next systemd attempt is neither active nor failed, so the
# two questions above cannot see it; this word is the one that shows the loop
# (measured 2026-09-25: one unit restarted 64 times while the run reported a
# successful deployment).
SYSTEMCTL_SHOW_RESULT_COMMAND: tuple[str, ...] = (
    "systemctl",
    "show",
    "--property",
    "Result",
    "--value",
    "{service_unit_name}",
)

# The word systemd reports for a unit whose last run ended cleanly. Every other
# word (exit-code, signal, timeout, oom-kill and the rest) means the run failed.
SUCCESSFUL_SERVICE_RESULT: str = "success"

# Readiness loop of the service after a start: attempts and pause between two
# checks. An inactive service is not a failure, because the service exits
# cleanly on a machine whose vault carries no port-forwarding data; only the
# failed state ends the loop as an error.
START_CHECK_ATTEMPTS: int = 10
START_CHECK_RETRY_DELAY_SECONDS: int = 1

# Syslog priority of a serious failure, 0 to 7.
ERROR_PRIORITY: int = 3

# The commands the deployed service runs itself. The own addresses come from the
# ip call below; the port-forwarding key is loaded into a dedicated agent,
# started by AGENT_START_COMMAND and filled by KEY_ADD_COMMAND. The collector is
# woken through the shared call of pyntara.metrics_collect, which reads its
# command from the values of the system_metrics_setup section, so the command
# exists once.
OWN_ADDRESSES_COMMAND: tuple[str, ...] = ("ip", "-o", "addr", "show")
AGENT_START_COMMAND: tuple[str, ...] = ("ssh-agent", "-s")
KEY_ADD_COMMAND: tuple[str, ...] = ("ssh-add", "{key_path}")

# The ssh call that holds one reverse tunnel open, with every argument that is
# ours to choose: the port, the options that keep the client from prompting or
# offering another identity, the keepalive and connect bounds of the values
# above, the key, the reverse forward and the destination. The remote side of
# the forward binds the address below, so a forwarded port is reachable through
# the server only.
SSH_FORWARD_COMMAND: tuple[str, ...] = (
    "ssh",
    "-p",
    "{ssh_port}",
    "-N",
    "-v",
    "-o",
    "ExitOnForwardFailure=yes",
    "-o",
    "IdentitiesOnly=yes",
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ServerAliveInterval={server_alive_interval_seconds}",
    "-o",
    "ServerAliveCountMax={server_alive_count_max}",
    "-o",
    "ConnectTimeout={connect_timeout_seconds}",
    "-i",
    "{key_path}",
    "-R",
    "{remote_port}:{remote_bind_address}:{local_port}",
    "{user}@{host}",
)
REMOTE_BIND_ADDRESS: str = "localhost"

# Names the service reads from or writes into the environment around the agent:
# the socket and the process id ssh-agent reports, the display the helper is
# given, the askpass variables of ssh and the variable that carries the
# passphrase to the helper. ASKPASS_ENV is the set of variables the unlock
# sets; {helper_path} is the helper script below.
AGENT_SOCKET_ENV_KEY: str = "SSH_AUTH_SOCK"
AGENT_PID_ENV_KEY: str = "SSH_AGENT_PID"
DISPLAY_ENV_KEY: str = "DISPLAY"
PASSPHRASE_ENV_KEY: str = "PF_KEY_PASSPHRASE"
ASKPASS_ENV: dict[str, str] = {
    "SSH_ASKPASS": "{helper_path}",
    "SSH_ASKPASS_REQUIRE": "force",
}

# The helper script of the unlock: written next to nothing else in its own
# temporary directory, it prints the passphrase variable and is removed right
# after the key is in the agent. The directory prefix and the file name are
# values, and the content must name PASSPHRASE_ENV_KEY.
ASKPASS_HELPER_DIR_PREFIX: str = "pyntara-pf-"
ASKPASS_HELPER_FILE_NAME: str = "askpass.sh"
ASKPASS_HELPER_CONTENT: str = '#!/bin/sh\necho "$PF_KEY_PASSPHRASE"\n'

# Seconds between two reads of the ssh output while the service waits for the
# server to confirm or refuse the forward, so the wait costs no spin.
FORWARD_OUTCOME_POLL_SECONDS: float = 0.2

# State file writing: the suffix of the temporary file the state is written
# through before it is moved into place, and the indentation of the JSON it
# carries.
STATE_TEMP_FILE_SUFFIX: str = ".tmp"
STATE_JSON_INDENT: int = 2

# Name the record of this channel carries in the network report, so a reader
# sees which network the reported ssh command goes through.
REPORT_CHANNEL_NAME: str = "port_forwarding"

# The names the task and the deployed service read. The list lives next to the
# values it names, so a module that stops declaring one of them is reported by
# name instead of raising while the run is under way.
READ_VALUE_NAMES: tuple[str, ...] = (
    "VAULT_GROUP_TITLE",
    "PASSPHRASE_ENTRY_TITLE",
    "REMOTE_SSH_USER",
    "DESIRED_PORT_MIN",
    "DESIRED_PORT_MAX",
    "SERVER_ALIVE_INTERVAL_SECONDS",
    "SERVER_ALIVE_COUNT_MAX",
    "ASKPASS_HELPER_FILE_MODE",
    "STATE_FILE_MODE",
    "CONNECT_TIMEOUT_SECONDS",
    "OWN_ADDRESSES_TIMEOUT_SECONDS",
    "AGENT_START_TIMEOUT_SECONDS",
    "KEY_UNLOCK_TIMEOUT_SECONDS",
    "KEY_CHECK_COMMAND",
    "KEY_CHECK_TIMEOUT_SECONDS",
    "KEY_CHECK_COMMAND",
    "KEY_CHECK_TIMEOUT_SECONDS",
    "ASKPASS_DISPLAY",
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_MULTIPLIER",
    "BACKOFF_MAX_SECONDS",
    "STATE_FILE_PATH",
    "SERVICE_UNIT_NAME",
    "SERVICE_RESTART_SECONDS",
    "JOURNAL_IDENTIFIER",
    "SERVICE_TEMPLATE_FILE_NAME",
    "SERVICE_MODULE_NAME",
    "MODULE_RUN_COMMAND",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
    "SYSTEMCTL_RESTART_COMMAND",
    "SYSTEMCTL_IS_FAILED_COMMAND",
    "SYSTEMCTL_SHOW_RESULT_COMMAND",
    "SUCCESSFUL_SERVICE_RESULT",
    "START_CHECK_ATTEMPTS",
    "START_CHECK_RETRY_DELAY_SECONDS",
    "ERROR_PRIORITY",
    "OWN_ADDRESSES_COMMAND",
    "AGENT_START_COMMAND",
    "KEY_ADD_COMMAND",
    "SSH_FORWARD_COMMAND",
    "REMOTE_BIND_ADDRESS",
    "AGENT_SOCKET_ENV_KEY",
    "AGENT_PID_ENV_KEY",
    "DISPLAY_ENV_KEY",
    "PASSPHRASE_ENV_KEY",
    "ASKPASS_ENV",
    "ASKPASS_HELPER_DIR_PREFIX",
    "ASKPASS_HELPER_FILE_NAME",
    "ASKPASS_HELPER_CONTENT",
    "FORWARD_OUTCOME_POLL_SECONDS",
    "STATE_TEMP_FILE_SUFFIX",
    "STATE_JSON_INDENT",
    "REPORT_CHANNEL_NAME",
)
