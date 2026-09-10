"""Unit tests for the shared UPnP port-forwarding helpers.

All external resources (the upnpc process) are mocked via monkeypatch;
the tests only touch temporary fixtures (docs/guides/developer-guide.md).
"""

from __future__ import annotations

from typing import Any

import pytest
from support import FakeProc as _FakeProc

from pyntara import upnp as upnp_module
from pyntara.upnp import (
    ensure_port_forwarding,
    forward_inbound_port,
    mapping_exists,
    parse_external_address,
    parse_port_mappings,
    router_external_address,
)

STATUS_OUTPUT = """upnpc: miniupnpc library test client
ExternalIPAddress = 190.55.165.52
Local LAN ip address : 192.168.1.5
"""

LIST_OUTPUT = """List of UPPE devices followed by port mappings
 0 TCP   443->192.168.1.5:443  'pyntara xray'  ''
 1 UDP   6881->192.168.1.5:6881  'other'  ''
"""


class TestParseExternalAddress:
    """Tests for reading the router address from the status output."""

    def test_reads_the_address(self) -> None:
        assert parse_external_address(STATUS_OUTPUT) == "190.55.165.52"

    def test_ignores_an_empty_address(self) -> None:
        # A router without a connection reports a zero address; it is not
        # an address a client could use.
        assert parse_external_address("ExternalIPAddress = 0.0.0.0\n") is None

    def test_ignores_a_router_that_does_not_answer(self) -> None:
        assert parse_external_address("No IGD UPnP Device found\n") is None


class TestParsePortMappings:
    """Tests for reading the mapping list."""

    def test_reads_both_mappings(self) -> None:
        assert parse_port_mappings(LIST_OUTPUT) == [
            ("TCP", 443, "192.168.1.5", 443),
            ("UDP", 6881, "192.168.1.5", 6881),
        ]

    def test_ignores_headers_and_notices(self) -> None:
        assert parse_port_mappings("No IGD UPnP Device found on the network\n") == []

    def test_finds_a_mapping_for_the_port(self) -> None:
        assert mapping_exists(LIST_OUTPUT, 443, "TCP") is True

    def test_reports_a_missing_mapping(self) -> None:
        assert mapping_exists(LIST_OUTPUT, 8443, "TCP") is False
        assert mapping_exists(LIST_OUTPUT, 443, "UDP") is False


class TestEnsurePortForwarding:
    """Tests for the forwarding request and its read-back."""

    def test_keeps_an_existing_mapping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A rerun must not add a second rule for the same port.
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
            commands.append(command)
            return _FakeProc(0, LIST_OUTPUT)

        monkeypatch.setattr(upnp_module, "run_command", fake_run)
        assert ensure_port_forwarding("upnpc", "d", "192.168.1.5", 443, "TCP", 30.0)
        assert all("-a" not in command for command in commands)

    def test_adds_the_mapping_and_reads_it_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # upnpc text output is not trusted on its own: the mapping list is
        # read again and decides.
        commands: list[list[str]] = []
        listing_calls = 0

        def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
            nonlocal listing_calls
            commands.append(command)
            if command[1] != "-l":
                return _FakeProc(0, "")
            listing_calls += 1
            if listing_calls == 1:
                return _FakeProc(0, LIST_OUTPUT.replace(" 0 TCP   443", " 0 TCP   8443"))
            return _FakeProc(0, LIST_OUTPUT)

        monkeypatch.setattr(upnp_module, "run_command", fake_run)
        assert ensure_port_forwarding(
            "upnpc", "pyntara xray", "192.168.1.5", 443, "TCP", 30.0
        )
        add = next(command for command in commands if "-a" in command)
        assert add[add.index("-a") + 1 :] == ["192.168.1.5", "443", "443", "TCP"]
        assert add[add.index("-e") + 1] == "pyntara xray"

    def test_reports_failure_when_the_router_refuses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            upnp_module, "run_command", lambda *a, **k: _FakeProc(1, "No IGD\n")
        )
        assert (
            ensure_port_forwarding("upnpc", "d", "192.168.1.5", 443, "TCP", 30.0)
            is False
        )

    def test_reports_no_address_without_a_router(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            upnp_module, "run_command", lambda *a, **k: _FakeProc(1, "No IGD\n")
        )
        assert router_external_address("upnpc", 30.0) is None


class TestForwardInboundPort:
    """Tests for the whole scenario shared by tasks."""

    def _requirements(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        router: str | None = "190.55.165.52",
        internal: str | None = "192.168.1.5",
        forwarded: bool = True,
    ) -> None:
        monkeypatch.setattr(
            upnp_module, "router_external_address", lambda _c, _t: router
        )
        monkeypatch.setattr(
            upnp_module, "default_route_address", lambda _t: internal
        )
        monkeypatch.setattr(
            upnp_module,
            "ensure_port_forwarding",
            lambda *_a, **_k: forwarded,
        )

    def test_returns_the_router_address_when_it_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._requirements(monkeypatch)
        assert (
            forward_inbound_port(
                "upnpc",
                "pyntara xray",
                443,
                "TCP",
                ("190.55.165.52",),
                30.0,
            )
            == "190.55.165.52"
        )

    def test_returns_the_router_address_without_observations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Without an observed address there is nothing to compare, so the
        # router answer is the only available source.
        self._requirements(monkeypatch)
        assert (
            forward_inbound_port("upnpc", "d", 443, "TCP", (), 30.0)
            == "190.55.165.52"
        )

    def test_refuses_the_router_address_behind_a_provider_nat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._requirements(monkeypatch, router="100.64.0.7")
        assert (
            forward_inbound_port(
                "upnpc",
                "d",
                443,
                "TCP",
                ("190.55.165.52",),
                30.0,
            )
            is None
        )

    def test_returns_nothing_without_a_router(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._requirements(monkeypatch, router=None)
        assert (
            forward_inbound_port("upnpc", "d", 443, "TCP", (), 30.0) is None
        )

    def test_returns_nothing_when_the_mapping_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._requirements(monkeypatch, forwarded=False)
        assert (
            forward_inbound_port("upnpc", "d", 443, "TCP", (), 30.0) is None
        )

    def test_returns_nothing_when_the_program_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The caller installs the program; the helper only runs it, so a
        # machine without it gets no address instead of an exception.
        def fail(command: list[str], **kwargs: Any) -> _FakeProc:
            raise FileNotFoundError("upnpc not found")

        monkeypatch.setattr(upnp_module, "run_command", fail)
        assert (
            forward_inbound_port("upnpc", "d", 443, "TCP", (), 30.0) is None
        )
