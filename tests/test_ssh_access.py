"""Unit tests for the ssh access command builder.

The builder is the single place that renders the ssh command of an
address, so every form the report carries is asserted here: the verbose
client, the always written port, the absent user and the proxy command
of an anonymity channel.
"""

from __future__ import annotations

from pyntara import ssh_access


def test_direct_command_carries_client_verbose_port_and_address() -> None:
    # A direct address needs no proxy; the port is written even when it
    # is the ssh default, so a reader never has to know a default.
    assert ssh_access.ssh_command("10.10.0.1", 30222) == "ssh -v -p 30222 10.10.0.1"
    assert ssh_access.ssh_command("example.onion", 22) == "ssh -v -p 22 example.onion"


def test_proxy_command_routes_through_the_local_socks_proxy() -> None:
    # An anonymity channel carries the SOCKS proxy of its own router, so
    # the connection leaves through the network the address belongs to.
    assert ssh_access.ssh_command(
        "address.b32.i2p", 30222, "127.0.0.1:4447"
    ) == (
        'ssh -v -p 30222 -o ProxyCommand="nc -X 5 -x 127.0.0.1:4447 %h %p"'
        " address.b32.i2p"
    )


def test_proxy_address_is_the_loopback_address_of_the_router() -> None:
    assert ssh_access.socks_proxy_address(9050) == "127.0.0.1:9050"


def test_link_scope_address_keeps_its_zone() -> None:
    # An IPv6 link scope address is reachable only through its own
    # interface, so the zone index travels with the target.
    assert ssh_access.ssh_command(
        "fe80::b1e1:869:8e81:2526%enp87s0", 30222
    ) == "ssh -v -p 30222 fe80::b1e1:869:8e81:2526%enp87s0"


def test_host_from_address_strips_a_url_scheme() -> None:
    assert ssh_access.host_from_address("https://vpn.example.com") == (
        "vpn.example.com"
    )
    assert ssh_access.host_from_address("169.58.51.98") == "169.58.51.98"
    assert ssh_access.host_from_address("2001:db8::1") == "2001:db8::1"
