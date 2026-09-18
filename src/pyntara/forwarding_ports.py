"""The deterministic forwarding ports of a machine.

Both forwarding schemes of the project name a machine by a port derived
from its hostname: the reverse tunnels of the auto port forwarding
service ask a server for the desired port of their machine, and the
router rule of the router port forwarding service publishes the same
number. The derivation and the order of its candidates live here once,
so the two services can never disagree about the number that names a
machine (docs/spec/port-forwarding-setup.md,
docs/spec/upnp-forwarding-setup.md).

desired_port is the port of one name, the first candidate of the chain.
candidate_ports walks the chain: the first candidate is the port of the
hostname itself, and every further candidate hashes the hostname with its
attempt number appended, so the order is the same on every run and on
every machine. The range comes from the values of the port_forwarding_setup section,
which both services read.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

from pyntara.values import port_forwarding_setup as values


def desired_port(hostname: str) -> int:
    """The deterministic port of one name.

    The port is a stable function of the name only: the same name gives
    the same port on every machine and in every run, so an operator can
    predict the number in advance. sha256 of the name is mapped into the
    declared range, and a name that carries an attempt number is a
    different name, which is what makes the chain below walk one range
    without repeating a port too soon.
    """

    value = int.from_bytes(hashlib.sha256(hostname.encode("utf-8")).digest()[:4], "big")
    span = values.DESIRED_PORT_MAX - values.DESIRED_PORT_MIN + 1
    return values.DESIRED_PORT_MIN + value % span


def candidate_ports(hostname: str) -> Iterator[int]:
    """Yield the candidate ports of a machine, in the order they are tried.

    The first candidate is desired_port of the hostname, and every
    further candidate is desired_port of the hostname with its attempt
    number appended, so the order is deterministic and needs no state to
    reproduce it. A candidate that repeats an earlier one is dropped
    instead of being offered twice. The range holds a finite number of
    ports, so the walk ends when every port of the range has been
    offered: the next candidate could only repeat one of them. A caller
    that needs a bounded list takes the first candidates it wants, which
    is what the router service does with its declared attempt count.
    """

    span = values.DESIRED_PORT_MAX - values.DESIRED_PORT_MIN + 1
    seen: set[int] = set()
    attempt = 1
    while len(seen) < span:
        salt = "" if attempt == 1 else str(attempt)
        port = desired_port(hostname + salt)
        if port not in seen:
            seen.add(port)
            yield port
        attempt += 1
