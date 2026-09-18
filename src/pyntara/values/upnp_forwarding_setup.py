"""Values of the upnp_forwarding_setup task and of its deployed service.

The task deploys the oneshot service upnp_forwarding.service and the timer that
runs it: the service asks the home router through UPnP to forward the SSH port
of this machine, so the machine stays reachable from the internet without a
manual rule, and the timer re-asserts the rule after a network change. The
external port comes from the same function that derives the remote port of the
port_forwarding task, so one machine carries one predictable number in both
schemes. A network without UPnP, a missing client and a router that refuses are
normal situations: they are reported and never fail the run.

The deployed service and the address command of the network report read the
values of this module directly, so the section has no copy in the config
document and no config path travels in the unit command line.
"""

from __future__ import annotations

# Package that provides the UPnP client.
UPNP_PACKAGE: str = "miniupnpc"

# Name of the upnpc binary the deployed service runs.
UPNP_CLIENT_COMMAND: str = "upnpc"

# Protocol of the forwarding rules this section owns; the client sends it as
# the protocol of the rule.
UPNP_PROTOCOL: str = "TCP"

# Description of the created rule, shown in the router interface and read back
# from the router, where it marks the rule as this machine's: a rule that
# carries another description is never touched. The {hostname} placeholder is
# replaced by the machine hostname, so two machines of this project on one
# router never take each other's rule.
UPNP_MAPPING_DESCRIPTION: str = "pyntara ssh {hostname}"

# Number of external ports the service tries before it gives up. The first
# candidate is the desired port of the machine; every further one hashes the
# hostname with the attempt number appended, so a port another rule already
# holds moves the attempt to a different part of the range instead of walking
# the neighbours of the first one.
MAPPING_ATTEMPTS: int = 11

# Name of the deployed oneshot service unit file and of the timer unit file that
# runs it.
SERVICE_UNIT_NAME: str = "upnp_forwarding.service"
TIMER_UNIT_NAME: str = "upnp_forwarding.timer"

# Names of the unit templates under task_data/upnp_forwarding_setup/ of the
# clone; the run fills their placeholders.
SERVICE_TEMPLATE_FILE_NAME: str = "upnp_forwarding.service"
TIMER_TEMPLATE_FILE_NAME: str = "upnp_forwarding.timer"

# Python module the deployed service unit runs from the shared deployment venv
# of system_metrics_setup.
SERVICE_MODULE_NAME: str = "pyntara.upnp_forwarding"

# Command the deployed unit runs: the interpreter of the deployed venv with the
# module above and the system config path. The values of this section come from
# the values package, but the service still reads the config for the sshd listen
# port of the ssh_daemon_setup section and for the call that wakes the report
# collector; the path leaves the command line when those sections move to the
# values package.
MODULE_RUN_COMMAND: tuple[str, ...] = ("{python}", "-m", "{module}", "{config_path}")

# The systemctl calls of the task, each carrying the unit name as its
# {unit_name} placeholder except the daemon reload.
SYSTEMCTL_DAEMON_RELOAD_COMMAND: tuple[str, ...] = ("systemctl", "daemon-reload")
SYSTEMCTL_ENABLE_COMMAND: tuple[str, ...] = (
    "systemctl",
    "enable",
    "{unit_name}",
)
SYSTEMCTL_START_COMMAND: tuple[str, ...] = (
    "systemctl",
    "start",
    "--no-block",
    "{unit_name}",
)
SYSTEMCTL_IS_FAILED_COMMAND: tuple[str, ...] = (
    "systemctl",
    "is-failed",
    "{unit_name}",
)

# Delay after boot before the first run and the pause between two runs. The
# first run waits for the network to come up; every later run re-asserts the
# rule, so a changed address of the machine or of the router heals by itself.
TIMER_BOOT_DELAY_SECONDS: int = 90
TIMER_INTERVAL_SECONDS: int = 900

# Journal identifier of the service. The service reports its trouble through
# the progress lines of the shared logger; it never needs a priority of its
# own, so the section declares none.
JOURNAL_IDENTIFIER: str = "upnp_forwarding"

# Name the channel carries in the network report, and the two scopes of the
# forwarded address: the internet reaches a global address, while an address of
# the provider network is reachable only from the networks that route to it.
REPORT_CHANNEL_NAME: str = "upnp"
GLOBAL_SCOPE_NAME: str = "global"
NAT_SCOPE_NAME: str = "nat"

# The names the task and the deployed service read. The list lives next to the
# values it names, so a module that stops declaring one of them is reported by
# name instead of raising while the run is under way.
READ_VALUE_NAMES: tuple[str, ...] = (
    "UPNP_PACKAGE",
    "UPNP_CLIENT_COMMAND",
    "UPNP_PROTOCOL",
    "UPNP_MAPPING_DESCRIPTION",
    "MAPPING_ATTEMPTS",
    "SERVICE_UNIT_NAME",
    "TIMER_UNIT_NAME",
    "SERVICE_TEMPLATE_FILE_NAME",
    "TIMER_TEMPLATE_FILE_NAME",
    "SERVICE_MODULE_NAME",
    "MODULE_RUN_COMMAND",
    "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
    "SYSTEMCTL_ENABLE_COMMAND",
    "SYSTEMCTL_START_COMMAND",
    "SYSTEMCTL_IS_FAILED_COMMAND",
    "TIMER_BOOT_DELAY_SECONDS",
    "TIMER_INTERVAL_SECONDS",
    "JOURNAL_IDENTIFIER",
    "REPORT_CHANNEL_NAME",
    "GLOBAL_SCOPE_NAME",
    "NAT_SCOPE_NAME",
)
