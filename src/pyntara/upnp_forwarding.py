"""Command: keep the router forwarding the SSH port of this machine.

The oneshot service deployed by the upnp_forwarding_setup task runs this
module, and the timer that runs the service repeats it after every network
change. The module asks the home router through UPnP to forward the SSH
daemon port of this machine, so the machine is reachable from the internet
without a manual rule, and it re-asserts that rule when the address of the
machine or the address of the router changed.

The external port is the desired port of the machine, derived from the
hostname by the same function the port_forwarding task uses, so one machine
asks for one predictable number in both schemes. When another rule already
holds that port, the next candidate hashes the hostname with the attempt
number appended, because this router replaces a rule silently and a foreign
rule is never touched; the number of attempts comes from the config.

The module changes the router only when it must: a rule that already
delivers the port to this machine is left alone, and a rule of another
program is never touched. When the router did change, the module wakes the
System Metrics collector, which rebuilds the network report and the
encrypted PDF and lets the running service send them. That is the rule of a
positive change: a new port or a new address is worth a fresh report, while
a rule that disappeared is not, because the next scheduled report carries
the current state anyway.

A machine without an UPnP router, without the client package, without an
address of its own, or with a router that refuses every candidate is a
machine that is simply not reachable this way. Every one of those is a
progress line and the module still exits 0, so the timer keeps running and
the user sees what happened instead of a crash.
Runs as `python -m pyntara.upnp_forwarding CONFIG_PATH`.
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from pyntara import upnp
from pyntara.config import (
    UPNP_FORWARDING_CONFIG_KEYS,
    Config,
    absent_config_keys,
    load_config,
)
from pyntara.logger import configure_journal
from pyntara.logger import log_progress as _log
from pyntara.metrics_collect import trigger_collection
from pyntara.port_forwarding import desired_port
from pyntara.public_address import default_route_address
from pyntara.ssh import ssh_port_from_directives
from pyntara.utils import install_package_once, package_is_installed


@dataclass(frozen=True)
class Forwarding:
    """The rule the router carries for this machine after the run.

    changed says whether this run touched the router: the mapping was
    missing, or it pointed at another address or another port. A rule that
    was already right leaves changed False, so a run that found the network
    unchanged wakes nobody.
    """

    external_port: int
    internal_port: int
    router_address: str
    changed: bool


def candidate_ports(cfg: Config, hostname: str) -> tuple[int, ...]:
    """The external ports to try, in the order they are tried.

    The first candidate is the desired port of the machine, the same number
    the reverse tunnel of the port_forwarding task asks a server for. Every
    further attempt hashes the hostname with its number appended, so a port
    that another rule holds moves the attempt to another part of the range
    instead of walking the neighbours of the first one. A hash that repeats
    an earlier candidate is dropped, so no attempt is spent twice on the
    same port.
    """

    attempts = cfg.upnp_forwarding_setup.mapping_attempts
    ports: list[int] = []
    for attempt in range(1, attempts + 1):
        salt = "" if attempt == 1 else str(attempt)
        port = desired_port(cfg, hostname + salt)
        if port not in ports:
            ports.append(port)
    return tuple(ports)


def ensure_client_package(cfg: Config) -> bool:
    """True when the UPnP client program is present, installing it if needed.

    The client is the external upnpc tool, which the provisioning task
    installs; the service checks it again, because a machine where the
    package was removed must still say why nothing is forwarded instead of
    failing with a Python error. A machine where the package cannot be
    installed keeps working without port forwarding, so the failure is a
    progress line and never a warning.
    """

    section = cfg.upnp_forwarding_setup
    timeout = cfg.engine.command_timeout_seconds
    if package_is_installed(cfg.engine, section.upnp_package, timeout):
        return True
    installed, error = install_package_once(
        cfg.engine, section.upnp_package, timeout
    )
    if not installed:
        _log(
            f"the UPnP client package {section.upnp_package} is not "
            f"available: {error}"
        )
        return False
    _log(f"UPnP client package {section.upnp_package} installed")
    return True


def ensure_forwarding(cfg: Config, hostname: str) -> Forwarding | None:
    """Make the router deliver the SSH port of this machine.

    Returns the rule that is in place and whether this run changed the
    router, or None when nothing can be forwarded here. The candidates are
    tried in order, and the description rendered from its template is the
    ownership mark of every rule: a rule that carries this mark and already
    delivers the port to this machine ends the search without touching the
    router, a rule that carries another mark and reaches another machine
    moves the search to the next candidate, and a rule of this machine whose
    target or mark is stale is written again, which the router does by
    itself.
    """

    section = cfg.upnp_forwarding_setup
    engine = cfg.engine
    timeout = engine.command_timeout_seconds
    command = section.upnp_client_command
    router_address = upnp.router_external_address(engine, command, timeout)
    if router_address is None:
        _log("no UPnP router on this network, the port is not forwarded")
        return None
    internal_address = default_route_address(engine, timeout)
    if internal_address is None:
        _log(
            "cannot read the address of this machine, "
            "the port is not forwarded"
        )
        return None
    internal_port = ssh_port_from_directives(cfg.ssh_daemon_setup)
    description = upnp.mapping_description(
        section.upnp_mapping_description, hostname
    )
    listing = upnp.list_mappings(engine, command, timeout)
    for port in candidate_ports(cfg, hostname):
        existing = upnp.mapping_for(
            listing,
            port,
            section.upnp_protocol,
            engine.upnpc_protocol_names,
            engine.upnpc_mapping_arrow,
        )
        target = (internal_address, internal_port)
        if existing is not None and (
            existing.internal_address,
            existing.internal_port,
        ) == target:
            if existing.description == description:
                _log(
                    f"the router already forwards port {port} to "
                    f"{internal_address}:{internal_port}"
                )
                return Forwarding(port, internal_port, router_address, False)
            _log(
                f"port {port} already reaches this machine under another "
                "description, the rule is written again"
            )
        elif (
            existing is not None
            and existing.description != description
        ):
            _log(
                f"port {port} carries the rule of another machine, "
                "trying the next port"
            )
            continue
        if upnp.ensure_port_forwarding(
            engine,
            command,
            description,
            internal_address,
            port,
            section.upnp_protocol,
            timeout,
            internal_port,
        ):
            _log(
                f"the router now forwards port {port} to "
                f"{internal_address}:{internal_port} "
                f"(router address {router_address})"
            )
            return Forwarding(port, internal_port, router_address, True)
        _log(f"the router refused port {port}, trying the next port")
    _log("every candidate port is taken by the rule of another machine")
    return None


def main(argv: list[str]) -> int:
    """Ensure the rule, and wake the report collector when it is new.

    Returns 2 on a wrong argument count and 1 when the section of the
    config lacks a value the service cannot work without, so an incomplete
    deployment is visible on the machine. Every other outcome is 0: the
    network states that leave the machine unreachable this way are reported
    as progress lines, because a machine without UPnP must keep working.
    """

    if len(argv) != 2:
        print(f"usage: {argv[0]} CONFIG_PATH", file=sys.stderr)
        return 2
    cfg = load_config(Path(argv[1]))
    section = cfg.upnp_forwarding_setup
    configure_journal(
        cfg.engine.with_journal_identifier(section.journal_identifier)
    )
    missing = absent_config_keys(section, UPNP_FORWARDING_CONFIG_KEYS)
    if missing:
        print(
            "error: the upnp_forwarding_setup section of the config has no "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    if not ensure_client_package(cfg):
        return 0
    forwarding = ensure_forwarding(cfg, socket.gethostname())
    if forwarding is not None and forwarding.changed:
        trigger_collection(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
