"""Shared UPnP port-forwarding helpers around the miniupnpc client.

The module wraps the external upnpc utility, which the calling task
installs before it runs, instead of implementing the UPnP IGD protocol:
upnpc lists the existing mappings, reports the address the router sees on
its internet side and asks the router for a mapping. A machine behind
such a router can then be reached from the internet even though its own
interface carries a private address.

The module installs nothing and knows no package names: it runs the
command it is given, together with the vocabulary of the client from the
[engine] table, so the calls, the field of the status output and the
format of the mapping list are values of the config.
"""

from __future__ import annotations

import ipaddress
import subprocess
from dataclasses import dataclass

from pyntara.config import EngineConfig
from pyntara.logger import log_progress
from pyntara.public_address import default_route_address
from pyntara.utils import run_command, substituted_command, trim_whitespace


def parse_external_address(
    text: str, address_key: str
) -> str | None:
    """The router internet address from the upnpc status output, or None.

    The field the address is printed under is a config value, so another
    client version that names it differently needs a config change. The
    value is accepted only when it is a valid IP address, so a line that
    reports something else (an error text) is not mistaken for an address,
    and an unspecified address (IPv4 0.0.0.0 or IPv6 ::) is refused by the
    standard library, because a router without a connection reports it.
    """

    for line in text.splitlines():
        if address_key not in line or "=" not in line:
            continue
        candidate = trim_whitespace(line.split("=", 1)[1])
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not address.is_unspecified:
            return candidate
    return None


@dataclass(frozen=True)
class PortMapping:
    """One rule of the router mapping table.

    The description is what tells the rules this project created from the
    rules of another program on the same router: the router keeps the text
    and prints it back, so it is the ownership mark of a rule. An empty
    description means the client that added the rule named none.
    """

    protocol: str
    external_port: int
    internal_address: str
    internal_port: int
    description: str


@dataclass(frozen=True)
class ForwardedAddress:
    """The router address that carries a forwarded port and its scope.

    globally_reachable is False when the address the router reports is not
    one of the addresses the internet sees: the provider runs another NAT
    above the router, so the mapping is reachable only from the networks
    that can route to that address. The address itself is returned in both
    cases, because it names the port that was really forwarded and a reader
    of the availability records must see it instead of nothing.
    """

    address: str
    globally_reachable: bool


def parse_port_mappings(
    text: str, protocol_names: tuple[str, ...], arrow: str
) -> list[PortMapping]:
    """The active mappings of the upnpc list output.

    Every mapping line looks like ` 1 TCP   443->192.168.1.2:443  'desc'`,
    so a line whose second field is one of the configured protocols and
    whose third field holds the configured arrow describes one mapping;
    anything else (headers, notices) is skipped. The description is the
    first quoted field of the line, so a description that carries spaces
    arrives whole.
    """

    protocols = {name.upper() for name in protocol_names}
    mappings: list[PortMapping] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        protocol = fields[1].upper()
        if protocol not in protocols:
            continue
        field = fields[2]
        if arrow not in field:
            continue
        external, _, internal = field.partition(arrow)
        internal_address, _, internal_port = internal.partition(":")
        if not external.isdigit() or not internal_port.isdigit():
            continue
        quoted = line.split("'")
        mappings.append(
            PortMapping(
                protocol=protocol,
                external_port=int(external),
                internal_address=internal_address,
                internal_port=int(internal_port),
                description=quoted[1] if len(quoted) >= 3 else "",
            )
        )
    return mappings


def mapping_for(
    text: str,
    port: int,
    protocol: str,
    protocol_names: tuple[str, ...],
    arrow: str,
) -> PortMapping | None:
    """The mapping that carries the external port, or None when it is free."""

    wanted = protocol.upper()
    for mapping in parse_port_mappings(text, protocol_names, arrow):
        if mapping.protocol == wanted and mapping.external_port == port:
            return mapping
    return None


def mapping_exists(
    text: str,
    port: int,
    protocol: str,
    protocol_names: tuple[str, ...],
    arrow: str,
) -> bool:
    """True when the list output already carries a mapping for the port."""

    return mapping_for(text, port, protocol, protocol_names, arrow) is not None


def router_external_address(
    engine: EngineConfig, command: str, timeout: float
) -> str | None:
    """The address the router reports for its internet side, or None.

    None means the router does not answer UPnP at all, which is the
    normal situation on a network with UPnP switched off.
    """

    try:
        result = run_command(
            substituted_command(
                engine.upnpc_status_command, {"command": command}
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return parse_external_address(result.stdout, engine.upnpc_external_address_key)


def list_mappings(
    engine: EngineConfig, command: str, timeout: float
) -> str:
    """The raw upnpc mapping list, empty when the router stays silent."""

    try:
        result = run_command(
            substituted_command(
                engine.upnpc_mapping_list_command, {"command": command}
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout


def forward_inbound_port(
    engine: EngineConfig,
    command: str,
    description: str,
    port: int,
    protocol: str,
    observed_addresses: tuple[str, ...],
    timeout: float,
    router_address: str | None = None,
) -> ForwardedAddress | None:
    """Forward the port through the router and return its address and scope.

    Every task that must be reachable from the internet needs the same
    steps, so they live here: ask the router for its internet address,
    forward the port to the address of the default route and read the
    mapping back. The caller installs the client package before calling,
    exactly as it installs its other packages; a machine without that
    package simply gets no address here. router_address carries an
    address the caller already read from the router, so a caller that
    forwards several ports reads the router once instead of once per
    port; None makes the call read it here.

    The router address is returned whenever the mapping is in place. A
    router that reports an address different from the addresses the
    caller observed sits behind another NAT: the caller learns that from
    globally_reachable and keeps the address instead of losing it. None
    means the utility is missing, no UPnP router answered, or the router
    refused the mapping; all three are normal situations reported as
    progress lines, never as failures.
    """

    if router_address is None:
        router_address = router_external_address(engine, command, timeout)
    if router_address is None:
        log_progress("no UPnP router on this network, port forwarding skipped")
        return None
    internal_address = default_route_address(engine, timeout)
    if internal_address is None:
        log_progress(
            "cannot read the default route address, port forwarding skipped"
        )
        return None
    if not ensure_port_forwarding(
        engine, command, description, internal_address, port, protocol, timeout
    ):
        log_progress(f"router refused the port {port} mapping")
        return None
    log_progress(
        f"router forwards port {port} to {internal_address} "
        f"(router address {router_address})"
    )
    globally_reachable = (
        not observed_addresses or router_address in observed_addresses
    )
    if not globally_reachable:
        log_progress(
            "router address differs from the observed address: the provider "
            "runs another NAT above the router, so the mapping is reachable "
            "only inside the provider network"
        )
    return ForwardedAddress(
        address=router_address, globally_reachable=globally_reachable
    )


def ensure_port_forwarding(
    engine: EngineConfig,
    command: str,
    description: str,
    internal_address: str,
    port: int,
    protocol: str,
    timeout: float,
) -> bool:
    """Ensure the router forwards the external port to this machine.

    An existing mapping for the port is kept, so a rerun never adds a
    second rule. Otherwise the mapping is requested and the result is
    read back from the router: upnpc reports its own success in text, and
    the mapping list is the honest answer to whether the rule exists.
    Returns whether the mapping is in place after the call.
    """

    if mapping_exists(
        list_mappings(engine, command, timeout),
        port,
        protocol,
        engine.upnpc_protocol_names,
        engine.upnpc_mapping_arrow,
    ):
        return True
    try:
        run_command(
            substituted_command(
                engine.upnpc_mapping_add_command,
                {
                    "command": command,
                    "description": description,
                    "internal_address": internal_address,
                    "port": str(port),
                    "protocol": protocol,
                },
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return mapping_exists(
        list_mappings(engine, command, timeout),
        port,
        protocol,
        engine.upnpc_protocol_names,
        engine.upnpc_mapping_arrow,
    )
