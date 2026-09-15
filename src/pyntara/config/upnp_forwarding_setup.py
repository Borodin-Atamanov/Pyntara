"""[upnp_forwarding_setup] table parser.

The section carries the parameters of the router port forwarding task and
service: the UPnP client package and binary, the protocol and the
description of the rules this machine owns, the number of external ports the
service tries, the deployed unit and timer parameters, and the names the
address carries in the network report. The port range and the port formula
are not configured here: they are the desired port range of the
[port_forwarding_setup] table and the shared function that maps a hostname
into it, so both tasks of a machine ask for the same number.
"""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpnpForwardingSetupConfig:
    """Router port forwarding parameters for the upnp_forwarding_setup task.

    upnp_package is the package that provides the client and
    upnp_client_command the binary the deployed service runs; upnp_protocol
    is the protocol of the rules this section owns and
    upnp_mapping_description the text that marks a rule as this project's,
    which the router keeps and prints back. mapping_attempts is how many
    external ports the service tries before it gives up: the first is the
    desired port of the machine, every further one hashes the hostname with
    the attempt number appended.

    service_unit_name and timer_unit_name name the deployed units,
    service_template_file_name and timer_template_file_name the templates
    under task_data/upnp_forwarding_setup/ of the clone, service_module_name
    the module the oneshot unit runs and module_run_command the command line
    that runs it from the deployment venv with the system config path. The
    four systemctl_* commands drive the units, each carrying the unit name
    as its {unit_name} placeholder except the daemon reload.
    timer_boot_delay_seconds delays the first run after boot and
    timer_interval_seconds is the pause between two runs, so a changed
    address or a rebooted router heals by itself. journal_identifier and
    error_priority control logging. report_channel_name names the channel in
    the network report; global_scope_name and nat_scope_name are the two
    scopes of the forwarded address, the second one for a router that itself
    sits behind another NAT, where the provider network is as far as the
    address reaches.
    """

    upnp_package: str
    upnp_client_command: str
    upnp_protocol: str
    upnp_mapping_description: str
    mapping_attempts: int
    service_unit_name: str
    timer_unit_name: str
    service_template_file_name: str
    timer_template_file_name: str
    service_module_name: str
    module_run_command: tuple[str, ...]
    systemctl_daemon_reload_command: tuple[str, ...]
    systemctl_enable_command: tuple[str, ...]
    systemctl_start_command: tuple[str, ...]
    systemctl_is_failed_command: tuple[str, ...]
    timer_boot_delay_seconds: int
    timer_interval_seconds: int
    journal_identifier: str
    error_priority: int
    report_channel_name: str
    global_scope_name: str
    nat_scope_name: str


# The keys of this table the deployed forwarding service and the address
# command of the network report read. The list lives next to the fields it
# names and both import it, so a config that lacks a value is reported by
# naming the key and not by a Python error (docs/spec/config-content.md,
# Where a value is declared and checked).
UPNP_FORWARDING_CONFIG_KEYS = (
    "upnp_package",
    "upnp_client_command",
    "upnp_protocol",
    "upnp_mapping_description",
    "mapping_attempts",
    "journal_identifier",
    "report_channel_name",
    "global_scope_name",
    "nat_scope_name",
)
