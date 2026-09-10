"""Shared discovery of the public addresses of the running machine.

The module asks a list of echo services for the address they see and
splits the answers into IPv4 and IPv6 lists. The caller passes the
service list and the timeout, and receives a frozen result, so tasks and
commands share one implementation instead of writing their own query.

The services are queried in parallel through a single curl process: a
sequential query would wait for every slow service in turn, while the
parallel call returns as soon as all transfers have finished and never
discards an answer that arrived. The machine is asked for both address
families at once, because a machine behind NAT often has no public IPv4
address but does have a public IPv6 address.
"""

from __future__ import annotations

import ipaddress
import subprocess
from dataclasses import dataclass


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


def _curl_command(services: tuple[str, ...], timeout_seconds: int) -> list[str]:
    """The one curl call that queries every service at the same time.

    --parallel runs the transfers together and --parallel-max keeps them
    all in flight; --write-out adds a newline after each answer, so the
    answers can be told apart even though they arrive interleaved. Each
    transfer is bounded by timeout_seconds, so the call takes at most
    that long even when a service never answers.
    """

    return [
        "curl",
        "--parallel",
        "--parallel-max",
        str(len(services)),
        "--silent",
        "--max-time",
        str(timeout_seconds),
        "--write-out",
        "\n",
        *services,
    ]


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
    services: tuple[str, ...],
    query_timeout_seconds: int,
    command_timeout_seconds: float,
) -> PublicAddresses:
    """Query every service in parallel and collect the addresses reported.

    All services run at the same time and the call returns when every
    transfer has finished, so one slow service delays the result by at
    most query_timeout_seconds and the answers that did arrive are kept.
    command_timeout_seconds bounds the whole process; a process that
    exceeds it is killed, so the call always returns. Returns empty lists
    when no service reports an address, when curl is missing or when the
    service list is empty.

    A nonzero curl exit code means at least one transfer failed (a
    service that is down or that answered too late); the answers of the
    other transfers are still in the output and are parsed, so the exit
    code decides nothing here and the caller sees exactly the addresses
    the machine could prove.
    """

    if not services:
        return PublicAddresses()
    try:
        process = subprocess.Popen(
            _curl_command(services, query_timeout_seconds),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return PublicAddresses()
    try:
        output, _ = process.communicate(timeout=command_timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
    return parse_public_addresses(output)
