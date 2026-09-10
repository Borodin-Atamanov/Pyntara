"""Shared UPnP port-forwarding helpers around the miniupnpc client.

The module wraps the external upnpc utility, which the calling task
installs before it runs, instead of implementing the UPnP IGD protocol:
upnpc lists the existing mappings, reports the address the router sees on
its internet side and asks the router for a mapping. A machine behind
such a router can then be reached from the internet even though its own
interface carries a private address.

The module installs nothing and knows no package names: it runs the
command it is given. Everything is best effort, so a missing utility or
a router that stays silent reports no mapping instead of raising.
"""

from __future__ import annotations

import ipaddress
import subprocess

from pyntara.logger import log_progress
from pyntara.public_address import default_route_address
from pyntara.utils import run_command, trim_whitespace

# The upnpc status output prints the router internet address as a
# key = value line; the value is the only part we read.
EXTERNAL_ADDRESS_KEY = "ExternalIPAddress"


def parse_external_address(text: str) -> str | None:
    """The router internet address from the upnpc status output, or None.

    The value is accepted only when it is a valid IP address, so a line
    that reports something else (a zero address, an error text) is not
    mistaken for an address.
    """

    for line in text.splitlines():
        if EXTERNAL_ADDRESS_KEY not in line or "=" not in line:
            continue
        candidate = trim_whitespace(line.split("=", 1)[1])
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if candidate != "0.0.0.0":
            return candidate
    return None


def parse_port_mappings(text: str) -> list[tuple[str, int, str, int]]:
    """The active mappings of the upnpc list output.

    Every mapping line looks like ` 1 TCP   443->192.168.1.2:443  'desc'`,
    so a line whose second field is a protocol and whose third field holds
    an arrow describes one mapping; anything else (headers, notices) is
    skipped. Returns tuples of (protocol, external port, internal
    address, internal port).
    """

    mappings: list[tuple[str, int, str, int]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        protocol = fields[1].upper()
        if protocol not in ("TCP", "UDP"):
            continue
        arrow = fields[2]
        if "->" not in arrow:
            continue
        external, _, internal = arrow.partition("->")
        internal_address, _, internal_port = internal.partition(":")
        if not external.isdigit() or not internal_port.isdigit():
            continue
        mappings.append(
            (protocol, int(external), internal_address, int(internal_port))
        )
    return mappings


def mapping_exists(text: str, port: int, protocol: str) -> bool:
    """True when the list output already carries a mapping for the port."""

    wanted = protocol.upper()
    return any(
        mapping_protocol == wanted and external_port == port
        for mapping_protocol, external_port, _, _ in parse_port_mappings(text)
    )


def router_external_address(command: str, timeout: float) -> str | None:
    """The address the router reports for its internet side, or None.

    None means the router does not answer UPnP at all, which is the
    normal situation on a network with UPnP switched off.
    """

    try:
        result = run_command(
            [command, "-s"], check=False, capture=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return parse_external_address(result.stdout)


def list_mappings(command: str, timeout: float) -> str:
    """The raw upnpc mapping list, empty when the router stays silent."""

    try:
        result = run_command(
            [command, "-l"], check=False, capture=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout


def forward_inbound_port(
    command: str,
    description: str,
    port: int,
    protocol: str,
    observed_addresses: tuple[str, ...],
    timeout: float,
    router_address: str | None = None,
) -> str | None:
    """Forward the port through the router and return its usable address.

    Every task that must be reachable from the internet needs the same
    steps, so they live here: ask the router for its internet address,
    forward the port to the address of the default route and read the
    mapping back. The caller installs the client package before calling,
    exactly as it installs its other packages; a machine without that
    package simply gets no address here. router_address carries an
    address the caller already read from the router, so a caller that
    forwards several ports reads the router once instead of once per
    port; None makes the call read it here. The router address is
    returned only when it can work, because a router that reports an
    address different from the addresses the caller observed sits behind
    another NAT and its mapping forwards nothing. None means the utility
    is missing, no UPnP router answered, the router refused the mapping,
    or another NAT sits above it; all four are normal situations reported
    as progress lines, never as failures.
    """

    if router_address is None:
        router_address = router_external_address(command, timeout)
    if router_address is None:
        log_progress("no UPnP router on this network, port forwarding skipped")
        return None
    internal_address = default_route_address(timeout)
    if internal_address is None:
        log_progress(
            "cannot read the default route address, port forwarding skipped"
        )
        return None
    if not ensure_port_forwarding(
        command, description, internal_address, port, protocol, timeout
    ):
        log_progress(f"router refused the port {port} mapping")
        return None
    log_progress(
        f"router forwards port {port} to {internal_address} "
        f"(router address {router_address})"
    )
    if observed_addresses and router_address not in observed_addresses:
        log_progress(
            "router address differs from the observed address: the provider "
            "runs another NAT above the router"
        )
        return None
    return router_address


def ensure_port_forwarding(
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

    if mapping_exists(list_mappings(command, timeout), port, protocol):
        return True
    try:
        run_command(
            [
                command,
                "-e",
                description,
                "-a",
                internal_address,
                str(port),
                str(port),
                protocol,
            ],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return mapping_exists(list_mappings(command, timeout), port, protocol)
