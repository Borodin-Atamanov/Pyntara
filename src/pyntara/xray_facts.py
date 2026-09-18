"""Run facts of the 3x-ui panel task: one query per value per run.

The three_x_ui_xray_setup task and its stages need the same facts about
this machine: the public addresses the echo services report, the
addresses of its interfaces and the address of the UPnP router. Reading
them once keeps a machine without UPnP fast, because a router that does
not answer is not asked again for every port, and it keeps the stages
free of address discovery of their own (docs/spec/3x-ui.md).

The module also resolves the host a client link must carry, in the order
that gives a caller the most usable address first: a public address that
really sits on this machine, the address the router forwards the port
to, the yggdrasil node address, the address of the local network, then
the share address the panel already stores.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from pyntara import upnp
from pyntara.logger import log_progress as _log
from pyntara.public_address import (
    PublicAddresses,
    fetch_public_addresses,
    local_addresses,
)
from pyntara.utils import (
    install_package_once,
    package_is_installed,
    trim_whitespace,
)
from pyntara.values import three_x_ui_xray_setup as panel_values
from pyntara.values import yggdrasil_service_setup as yggdrasil_values


@dataclass(frozen=True)
class _RunFacts:
    """Values collected once per run and reused by every stage.

    public_addresses and local_addresses come from one query each instead
    of one query per stage, and the router address is read from the UPnP
    router once per run: a router that does not answer at all is not asked
    again for every port, which keeps a machine without UPnP fast.
    """

    public_addresses: PublicAddresses
    local_addresses: tuple[str, ...]
    router_address: str | None
    client_address: str | None = None


def _public_addresses(timeout: float) -> PublicAddresses:
    """The public addresses the configured echo services report.

    The shared helper does the parallel query, so the task only decides
    what to do with the addresses.
    """

    return fetch_public_addresses(
        panel_values.SERVER_IP_SERVICES,
        panel_values.SERVER_IP_TIMEOUT_SECONDS,
        timeout,
    )


def _collect_run_facts(timeout: float) -> _RunFacts:
    """Collect the addresses and the router address once for this run.

    The UPnP client package is installed here, before the first stage
    that may use it; the shared helper installs nothing itself. A router
    that does not answer is reported once, and the port forwarding is
    skipped for the rest of the run instead of retrying on every port.
    A machine that owns a public address needs neither: the address is
    reachable directly, so the client package is not installed and the
    router is never asked.
    """

    public = _public_addresses(timeout)
    local = local_addresses(timeout)
    router_address: str | None = None
    if panel_values.UPNP_ENABLED:
        if _machine_public_address(public, local) is not None:
            _log(
                "a public address sits on this machine, "
                "the router needs no port forwarding"
            )
        elif _ensure_upnp_client(timeout):
            _log("looking for a UPnP router")
            router_address = upnp.router_external_address(
                panel_values.UPNP_CLIENT_COMMAND, timeout
            )
            if router_address is None:
                _log("no UPnP router on this network, port forwarding is skipped")
            else:
                _log(f"UPnP router reports its internet address {router_address}")
    return _RunFacts(
        public_addresses=public,
        local_addresses=local,
        router_address=router_address,
    )


def _forward_upnp_ports(
    facts: _RunFacts,
    timeout: float,
) -> str | None:
    """Forward the inbound and the ACME port once, and report the host.

    The inbound port makes the node reachable for its clients; the ACME
    port makes a trusted certificate possible. Both use the router
    address already read for this run, so the router is asked once. The
    returned address is the one a client link may use, so it is returned
    only when the internet really reaches it; an address that belongs to
    the provider network is reported in the journal instead, because a
    client outside that network cannot reach it, while a client inside it
    can read the address there.
    """

    if facts.router_address is None:
        return None
    observed = (*facts.public_addresses.ipv4, *facts.public_addresses.ipv6)
    description = upnp.mapping_description(
        panel_values.UPNP_MAPPING_DESCRIPTION, socket.gethostname()
    )
    _log(f"asking the router to forward port {panel_values.INBOUND_PORT} for clients")
    forwarded = upnp.forward_inbound_port(
        panel_values.UPNP_CLIENT_COMMAND,
        description,
        panel_values.INBOUND_PORT,
        panel_values.UPNP_PROTOCOL,
        observed,
        timeout,
        facts.router_address,
    )
    if panel_values.SSL_ENABLED:
        _log(
            f"asking the router to forward port {panel_values.ACME_PORT} "
            "for the certificate challenge"
        )
        upnp.forward_inbound_port(
            panel_values.UPNP_CLIENT_COMMAND,
            description,
            panel_values.ACME_PORT,
            panel_values.UPNP_PROTOCOL,
            (),
            timeout,
            facts.router_address,
        )
    if forwarded is None:
        return None
    if not forwarded.globally_reachable:
        _log(
            f"the router forwards port {panel_values.INBOUND_PORT} at "
            f"{forwarded.address}, reachable only inside the provider network"
        )
        return None
    return forwarded.address


def _ensure_upnp_client(timeout: float) -> bool:
    """True when the UPnP client program is present, installing it if needed.

    The port forwarding uses the external upnpc tool, exactly like the
    self-signed certificate uses openssl: the package is installed here,
    next to the other packages the task needs, so the shared helper only
    runs a program that exists. A machine where the package cannot be
    installed keeps working without port forwarding, so the failure is a
    progress line and never a warning.
    """

    if package_is_installed(panel_values.UPNP_PACKAGE, timeout):
        return True
    installed, error = install_package_once(panel_values.UPNP_PACKAGE, timeout)
    if not installed:
        _log(f"UPnP client package {panel_values.UPNP_PACKAGE} is unavailable: {error}")
        return False
    _log(f"UPnP client package {panel_values.UPNP_PACKAGE} installed")
    return True


def _detect_server_ip(facts: _RunFacts) -> str | None:
    """The public IPv4 address of this machine, or None.

    The SSL stage needs an IPv4 address, because a Let's Encrypt IP
    certificate is issued for an IPv4 address; a machine whose public
    address is IPv6 only keeps its self-signed certificate. The address
    comes from the run facts, so no further query is made.
    """

    ipv4 = facts.public_addresses.ipv4
    if ipv4:
        return ipv4[0]
    _log("no echo service reported a public IPv4 address")
    return None


def _yggdrasil_address() -> str | None:
    """The yggdrasil node address saved by the yggdrasil task, or None.

    The address file is written by yggdrasil_service_setup after the node
    joins the mesh; it is world-readable, so a machine behind NAT can use
    its mesh address as the server address for client links.
    """

    try:
        text = yggdrasil_values.ADDRESS_FILE_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    address = trim_whitespace(text)
    return address or None


def _canonical_share_address(address: str) -> str:
    """The address in the form the panel stores in its share address.

    The panel wraps an IPv6 address in brackets; writing the bracketed
    form keeps the comparison for changed honest, because the value read
    back from the panel equals the written one.
    """

    cleaned = trim_whitespace(address)
    if ":" in cleaned and not cleaned.startswith("["):
        return f"[{cleaned}]"
    return cleaned


def _bare_address(address: str) -> str:
    """The address without the brackets an IPv6 share address carries.

    Client configurations take the address without brackets; the panel
    adds them only when it renders a share link.
    """

    return trim_whitespace(address).strip("[]")


def _machine_public_address(
    addresses: PublicAddresses, local: tuple[str, ...]
) -> str | None:
    """The public address that really sits on this machine, or None.

    An echo service reports the address the internet sees; that address
    belongs to this machine only when it also appears among the local
    addresses, which means nothing translates it on the way and the
    machine is reachable from the internet directly. None when every
    reported address belongs to a provider or to a router instead, so
    the caller knows a NAT sits in front.
    """

    for candidate in (*addresses.ipv4, *addresses.ipv6):
        if candidate in local:
            return candidate
    return None


def _server_share_address(
    inbound: dict[str, object],
    facts: _RunFacts,
) -> str | None:
    """The host a client link must carry, in the panel's own form.

    Priority: a public address that really belongs to this machine, then
    the router address when UPnP forwards the port to us, then the
    yggdrasil node address (better than a LAN address, because another
    node reaches it over the mesh), then the LAN address of this machine,
    then the share address the panel already stores. None when none of
    them is available, so the caller reports it instead of writing a
    guess. Every value comes from the run facts, so nothing is queried
    twice.
    """

    local = facts.local_addresses
    own_address = _machine_public_address(facts.public_addresses, local)
    if own_address is not None:
        return _canonical_share_address(own_address)
    if facts.client_address is not None:
        return _canonical_share_address(facts.client_address)
    node_address = _yggdrasil_address()
    if node_address is not None:
        _log(f"using the yggdrasil node address as the share host: {node_address}")
        return _canonical_share_address(node_address)
    if local:
        _log(f"using the local address as the share host: {local[0]}")
        return _canonical_share_address(local[0])
    stored = inbound.get("shareAddr")
    if isinstance(stored, str) and trim_whitespace(stored):
        _log("keeping the share address already stored in the panel")
        return trim_whitespace(stored)
    return None
