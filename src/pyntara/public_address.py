"""Shared discovery of the public addresses of the running machine.

The module asks a list of echo services for the address they see and
splits the answers into IPv4 and IPv6 lists. The caller passes the
service list and the timeout, and receives a frozen result, so tasks and
commands share one implementation instead of writing their own query.

The services are queried in parallel through the shared curl helper of
utils: a sequential query would wait for every slow service in turn,
while the parallel call returns as soon as all transfers have finished
and never discards an answer that arrived. The same helper answers the
country question in pyntara.location, so both services share one query
shape and one timeout policy. The machine is asked for both address
families at once, because a machine behind NAT often has no public IPv4
address but does have a public IPv6 address.
"""

from __future__ import annotations

import ipaddress
import subprocess
from dataclasses import dataclass

from pyntara.config import EngineConfig
from pyntara.utils import fetch_urls_in_parallel, run_command


@dataclass(frozen=True)
class PublicAddresses:
    """Public addresses reported by the echo services, by address family.

    The order follows the answers and duplicates are removed, so a
    service that reports the same address twice contributes once.
    """

    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when no service reported any address."""

        return not self.ipv4 and not self.ipv6


def local_addresses(timeout: float) -> tuple[str, ...]:
    """Every global-scope address of the machine interfaces.

    Parsed from `ip -o addr show scope global`; loopback and link-local
    addresses fall outside that scope. The addresses tell whether an
    address reported by an echo service really belongs to this machine (a
    white address) or the machine sits behind NAT.
    """

    try:
        result = run_command(
            ["ip", "-o", "addr", "show", "scope", "global"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ()
    addresses: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        for index, field in enumerate(fields):
            if field not in ("inet", "inet6") or index + 1 >= len(fields):
                continue
            candidate = fields[index + 1].split("/", 1)[0]
            if candidate and candidate not in addresses:
                addresses.append(candidate)
    return tuple(addresses)


def directly_connected_networks(timeout: float) -> tuple[str, ...]:
    """Every subnet the kernel reports as directly connected, in order.

    Parsed from the kernel routes of both address families
    (`ip -o -4 route show proto kernel` and its -6 counterpart). These are
    the machine's own networks: a local network, a bridge and the
    yggdrasil overlay all appear here, so a routing policy can send them
    to the direct outbound whatever range they use. The subnets are read
    from the kernel instead of being configured, so a machine with an
    unusual local range is still handled correctly.
    """

    networks: list[str] = []
    for family in ("-4", "-6"):
        try:
            result = run_command(
                ["ip", "-o", family, "route", "show", "proto", "kernel"],
                check=False,
                capture=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        for line in result.stdout.splitlines():
            fields = line.split()
            if not fields or "/" not in fields[0]:
                continue
            network = fields[0]
            if network not in networks:
                networks.append(network)
    return tuple(networks)


def default_route_address(timeout: float) -> str | None:
    """The address the machine uses to reach the internet, or None.

    The default route carries the source address of the outgoing
    connection, which is the address behind a router that another host
    must reach: the interface that actually leaves the local network.
    """

    try:
        result = run_command(
            ["ip", "-4", "route", "show", "default"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    for line in result.stdout.splitlines():
        fields = line.split()
        for index, field in enumerate(fields):
            if field == "src" and index + 1 < len(fields):
                return fields[index + 1]
    return None


def parse_public_addresses(text: str) -> PublicAddresses:
    """Split the answers of the echo services into address families.

    Every whitespace-separated token is parsed as an IP address; a token
    that is not an address (an HTML error page, a hostname) is skipped,
    and a valid address goes to the list of its own family with the
    repeats merged.
    """

    ipv4: list[str] = []
    ipv6: list[str] = []
    for token in text.split():
        candidate = token.strip().strip('"')
        if not candidate:
            continue
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        target = ipv4 if parsed.version == 4 else ipv6
        if candidate not in target:
            target.append(candidate)
    return PublicAddresses(tuple(ipv4), tuple(ipv6))


def fetch_public_addresses(
    engine: EngineConfig,
    services: tuple[str, ...],
    query_timeout_seconds: int,
    command_timeout_seconds: float,
) -> PublicAddresses:
    """Query every service in parallel and collect the addresses reported.

    The parallel query itself is the shared fetch_urls_in_parallel helper,
    so the query shape and its timeout policy live in one place and the
    country detection uses the same mechanism. All services run at the
    same time and the call returns when every transfer has finished, so
    one slow service delays the result by at most query_timeout_seconds
    and the answers that did arrive are kept. command_timeout_seconds
    bounds the whole process, so the call always returns. Returns empty
    lists when no service reports an address, when curl is missing or when
    the service list is empty.
    """

    if not services:
        return PublicAddresses()
    return parse_public_addresses(
        fetch_urls_in_parallel(
            engine, services, query_timeout_seconds, command_timeout_seconds
        )
    )
