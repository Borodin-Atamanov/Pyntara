"""Unit tests for the shared public-address collection.

All external resources (the curl process) are mocked via monkeypatch;
the tests only touch temporary fixtures (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import pytest
from support import make_config

from pyntara import public_address as public_address_module
from pyntara.public_address import (
    PublicAddresses,
    default_route_address,
    directly_connected_networks,
    fetch_public_addresses,
    local_addresses,
    parse_public_addresses,
)

SERVICES = ("https://api4.ipify.org", "https://ipv6.ipify.org")


class _Completed:
    """A run_command result double carrying only the captured output."""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


class TestParsePublicAddresses:
    """Tests for the parsing and the duplicate merging."""

    def test_splits_the_families_and_merges_repeats(self) -> None:
        # One address per line, several services reporting the same
        # address: every unique address stays, the repeats are merged.
        text = "203.0.113.5\n203.0.113.5\n2001:db8::1\n203.0.113.5\n"
        assert parse_public_addresses(text) == PublicAddresses(
            ipv4=("203.0.113.5",),
            ipv6=("2001:db8::1",),
        )

    def test_ignores_text_that_is_not_an_address(self) -> None:
        # An error page or a banner must never be reported as an address.
        text = "<html>error</html>\nnot-an-address\n203.0.113.9\n"
        assert parse_public_addresses(text) == PublicAddresses(
            ipv4=("203.0.113.9",)
        )

    def test_keeps_the_arrival_order(self) -> None:
        text = "198.51.100.9 203.0.113.5 198.51.100.9\n"
        assert parse_public_addresses(text).ipv4 == ("198.51.100.9", "203.0.113.5")

    def test_empty_text_reports_nothing(self) -> None:
        assert parse_public_addresses("").is_empty is True


class TestLocalAddresses:
    """Tests for reading the machine interface addresses."""

    def test_collects_ipv4_and_ipv6_without_repeats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = (
            "2: enp1s0    inet 10.10.0.1/24 brd 10.10.0.255 scope global enp1s0\n"
            "3: wlp1s0    inet 192.168.1.5/24 scope global dynamic wlp1s0\n"
            "3: wlp1s0    inet6 2001:db8::5/64 scope global\n"
            "3: wlp1s0    inet6 2001:db8::5/64 scope global\n"
        )
        monkeypatch.setattr(
            public_address_module,
            "run_command",
            lambda *a, **k: _Completed(output),
        )
        assert local_addresses(30.0) == ("10.10.0.1", "192.168.1.5", "2001:db8::5")

    def test_reports_nothing_without_the_ip_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(command: object, **kwargs: object) -> object:
            raise OSError("ip not found")

        monkeypatch.setattr(public_address_module, "run_command", fail)
        assert local_addresses(30.0) == ()


class TestDirectlyConnectedNetworks:
    """Tests for reading the machine's own subnets."""

    def test_collects_both_families_without_repeats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outputs = {
            "-4": (
                "10.10.0.0/24 dev enp1s0 proto kernel scope link src 10.10.0.1\n"
                "127.0.0.0/8 dev lo proto kernel scope link src 127.0.0.1\n"
            ),
            "-6": (
                "200::/7 dev ygg proto kernel metric 256 pref medium\n"
                "fe80::/64 dev ygg proto kernel metric 256 pref medium\n"
            ),
        }

        def fake_run(command: list[str], **kwargs: object) -> _Completed:
            # The command is ip -o <family> route show proto kernel.
            family = command[2]
            return _Completed(outputs[family])

        monkeypatch.setattr(public_address_module, "run_command", fake_run)
        assert directly_connected_networks(30.0) == (
            "10.10.0.0/24",
            "127.0.0.0/8",
            "200::/7",
            "fe80::/64",
        )

    def test_reports_nothing_without_the_ip_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(command: object, **kwargs: object) -> object:
            raise OSError("ip not found")

        monkeypatch.setattr(public_address_module, "run_command", fail)
        assert directly_connected_networks(30.0) == ()


class TestDefaultRouteAddress:
    """Tests for reading the address that reaches the router."""

    def test_reads_the_source_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = (
            "default via 192.168.1.1 dev wlp1s0 proto dhcp src 192.168.1.5 "
            "metric 600\n"
        )
        monkeypatch.setattr(
            public_address_module,
            "run_command",
            lambda *a, **k: _Completed(output),
        )
        assert default_route_address(30.0) == "192.168.1.5"

    def test_reports_nothing_without_a_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            public_address_module,
            "run_command",
            lambda *a, **k: _Completed(""),
        )
        assert default_route_address(30.0) is None


class TestCollectPublicAddresses:
    """Tests for the delegation to the shared parallel query."""

    def test_queries_the_services_through_the_shared_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The module delegates the query, so the shape of the parallel
        # curl call and its timeout policy live in utils alone and the
        # country detection uses exactly the same mechanism.
        calls: list[tuple[tuple[str, ...], int, float]] = []

        def fake_fetch(
            engine: object,
            urls: tuple[str, ...],
            query_timeout: int,
            command_timeout: float,
        ) -> str:
            calls.append((urls, query_timeout, command_timeout))
            return "203.0.113.5\n203.0.113.5\n2001:db8::1\n"

        monkeypatch.setattr(
            public_address_module, "fetch_urls_in_parallel", fake_fetch
        )
        addresses = fetch_public_addresses(make_config().engine, SERVICES, 60, 1800.0)
        assert addresses == PublicAddresses(
            ipv4=("203.0.113.5",),
            ipv6=("2001:db8::1",),
        )
        assert calls == [(SERVICES, 60, 1800.0)]

    def test_returns_nothing_when_the_service_list_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty list must not start a query at all.
        def fail_fetch(*args: object, **kwargs: object) -> str:
            raise AssertionError("no query expected")

        monkeypatch.setattr(
            public_address_module, "fetch_urls_in_parallel", fail_fetch
        )
        assert fetch_public_addresses(make_config().engine, (), 60, 1800.0).is_empty is True
