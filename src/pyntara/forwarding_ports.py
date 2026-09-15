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
every machine. The range comes from the [port_forwarding_setup] table,
which both services read.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

from pyntara.config import Config


def desired_port(cfg: Config, hostname: str) -> int:
    """The deterministic port of one name.

    The port is a stable function of the name only: the same name gives
    the same port on every machine and in every run, so an operator can
    predict the number in advance. sha256 of the name is mapped into the
    configured range, and a name that carries an attempt number is a
    different name, which is what makes the chain below walk one range
    without repeating a port too soon.
    """

    pf = cfg.port_forwarding_setup
    value = int.from_bytes(hashlib.sha256(hostname.encode("utf-8")).digest()[:4], "big")
    span = pf.desired_port_max - pf.desired_port_min + 1
    return pf.desired_port_min + value % span


def candidate_ports(cfg: Config, hostname: str) -> Iterator[int]:
    """Yield the candidate ports of a machine, in the order they are tried.

    The first candidate is desired_port of the hostname, and every
    further candidate is desired_port of the hostname with its attempt
    number appended, so the order is deterministic and needs no state to
    reproduce it. A candidate that repeats an earlier one is dropped
    instead of being offered twice. The range holds a finite number of
    ports, so the walk ends when every port of the range has been
    offered: the next candidate could only repeat one of them. A caller
    that needs a bounded list takes the first candidates it wants, which
    is what the router service does with its configured attempt count.
    """

    pf = cfg.port_forwarding_setup
    span = pf.desired_port_max - pf.desired_port_min + 1
    seen: set[int] = set()
    attempt = 1
    while len(seen) < span:
        salt = "" if attempt == 1 else str(attempt)
        port = desired_port(cfg, hostname + salt)
        if port not in seen:
            seen.add(port)
            yield port
        attempt += 1
