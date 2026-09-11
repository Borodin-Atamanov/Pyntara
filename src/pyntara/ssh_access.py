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

# The client and its verbose flag. -v prints the detailed connection
# messages, so a failing link shows where it stops; the name is written
# in full because the reader copies the line into a shell of another
# machine.
SSH_CLIENT = "ssh"
SSH_VERBOSE_FLAG = "-v"

# Host part of the local SOCKS proxy of the anonymity routers.
ANONYMITY_PROXY_HOST = "127.0.0.1"

# The netcat client of the operator machine routes the connection
# through that proxy; %h and %p are the ssh placeholders for the target
# host and port (docs/spec/i2pd-service.md, section Connecting over I2P).
SOCKS_PROXY_COMMAND = "nc -X 5 -x {proxy} %h %p"


def socks_proxy_address(port: int) -> str:
    """The local SOCKS proxy address of an anonymity router."""

    return f"{ANONYMITY_PROXY_HOST}:{port}"


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


def ssh_command(address: str, port: int, socks_proxy: str | None = None) -> str:
    """The ssh command that reaches address on port.

    The port is always written, so a reader never has to know a default.
    socks_proxy adds the ProxyCommand of the anonymity network the
    address belongs to; None builds a direct connection. An address that
    carries a zone index (fe80::1%eth0) is used as it is, because the
    zone is what makes a link scope address reachable.
    """

    parts = [SSH_CLIENT, SSH_VERBOSE_FLAG, "-p", str(port)]
    if socks_proxy:
        proxy_command = SOCKS_PROXY_COMMAND.format(proxy=socks_proxy)
        parts += ["-o", f'ProxyCommand="{proxy_command}"']
    parts.append(address)
    return " ".join(parts)
