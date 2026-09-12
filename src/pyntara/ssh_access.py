"""Shared construction of the ssh access command of one address.

The network telemetry reports, for every address of the machine and for
every channel that reaches it, the ssh command a person runs to connect
from anywhere (docs/spec/system-metrics.md, section Report collector).
The command is built here and nowhere else, so its form lives in one
place: the client is always verbose, the port is always written even
when it equals the default, the user is deliberately absent (the
operator chooses it and the ssh agent offers the deployed key), and an
anonymity channel carries the local SOCKS proxy of its own router
through ProxyCommand.

The proxy host is the loopback address, because i2pd and Tor bind their
SOCKS proxy to the loopback interface only: the client of an anonymity
network runs on the machine the report describes, and the address of
that machine under the network comes from the channel itself
(docs/spec/i2pd-service.md, section Connecting over I2P;
docs/spec/tor-service.md, section Connecting over Tor).
"""

from __future__ import annotations

from urllib.parse import urlparse

from pyntara.config import EngineConfig


def socks_proxy_address(engine: EngineConfig, port: int) -> str:
    """The local SOCKS proxy address of an anonymity router.

    The host comes from the engine, so the two routers and every reported
    command share one spelling of the loopback address the proxies bind.
    """

    return f"{engine.ssh_report_proxy_host}:{port}"


def host_from_address(address: str) -> str:
    """The host of an address that may be written as a url.

    A url like https://vpn.example.com resolves to vpn.example.com; a
    bare ipv4, ipv6 or hostname is returned unchanged. The host is what
    a connection target needs, so a vault entry that carries a url and
    an entry that carries an address produce the same command.
    """

    if "://" in address:
        parsed = urlparse(address)
        if parsed.hostname:
            return parsed.hostname
    return address


def ssh_command(
    engine: EngineConfig,
    address: str,
    port: int,
    socks_proxy: str | None = None,
) -> str:
    """The ssh command that reaches address on port.

    The command is built from the engine texts, so its form lives in the
    config: the client is verbose, the port is always written so a reader
    never has to know a default, and socks_proxy adds the ProxyCommand of
    the anonymity network the address belongs to. An address that carries
    a zone index (fe80::1%eth0) is used as it is, because the zone is what
    makes a link scope address reachable.
    """

    proxy_option = ""
    if socks_proxy:
        proxy_command = engine.ssh_report_socks_command_format.format(
            proxy=socks_proxy
        )
        proxy_option = engine.ssh_report_proxy_option_format.format(
            proxy_command=proxy_command
        )
    return engine.ssh_report_command_format.format(
        port=port, address=address, proxy_option=proxy_option
    )
