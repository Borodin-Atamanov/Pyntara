"""Unit tests for the shared public-address collection.

All external resources (the curl process) are mocked via monkeypatch;
the tests only touch temporary fixtures (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from pyntara import public_address as public_address_module
from pyntara.public_address import (
    PublicAddresses,
    default_route_address,
    fetch_public_addresses,
    local_addresses,
    parse_public_addresses,
)

SERVICES = ("https://api4.ipify.org", "https://ipv6.ipify.org")


class _Process:
    """A Popen double that returns a fixed output from communicate."""

    def __init__(self, output: str) -> None:
        self.output = output
        self.killed = False
        self.timeout_used: float | None = None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.timeout_used = timeout
        return (self.output, "")

    def kill(self) -> None:
        self.killed = True


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
    """Tests for the parallel query through the shared helper."""

    def test_queries_all_services_in_one_parallel_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every service runs in the same curl process, so a slow service
        # cannot delay the others one by one.
        commands: list[list[str]] = []
        process = _Process("203.0.113.5\n203.0.113.5\n2001:db8::1\n")

        def fake_popen(command: list[str], **kwargs: Any) -> _Process:
            commands.append(command)
            return process

        monkeypatch.setattr(public_address_module.subprocess, "Popen", fake_popen)
        addresses = fetch_public_addresses(SERVICES, 60, 1800.0)
        assert addresses == PublicAddresses(
            ipv4=("203.0.113.5",),
            ipv6=("2001:db8::1",),
        )
        command = commands[0]
        assert "--parallel" in command
        assert command[command.index("--max-time") + 1] == "60"
        assert command[-len(SERVICES) :] == list(SERVICES)
        assert process.timeout_used == 1800.0

    def test_returns_nothing_when_the_service_list_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty list must not start a process at all.
        def fail_popen(command: list[str], **kwargs: Any) -> _Process:
            raise AssertionError("no process expected")

        monkeypatch.setattr(public_address_module.subprocess, "Popen", fail_popen)
        assert fetch_public_addresses((), 60, 1800.0).is_empty is True

    def test_returns_nothing_when_curl_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_popen(command: list[str], **kwargs: Any) -> _Process:
            raise OSError("curl not found")

        monkeypatch.setattr(public_address_module.subprocess, "Popen", fail_popen)
        assert fetch_public_addresses(SERVICES, 60, 1800.0).is_empty is True

    def test_kills_the_process_when_the_command_timeout_expires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The command timeout bounds the call even when curl never ends.
        process = _Process("203.0.113.5\n")

        def communicate_with_timeout(
            timeout: float | None = None,
        ) -> tuple[str, str]:
            process.timeout_used = timeout
            if not process.killed:
                raise subprocess.TimeoutExpired("curl", timeout or 0)
            return (process.output, "")

        process.communicate = communicate_with_timeout  # type: ignore[method-assign]
        monkeypatch.setattr(
            public_address_module.subprocess, "Popen", lambda *a, **k: process
        )
        addresses = fetch_public_addresses(SERVICES, 60, 1800.0)
        assert addresses.ipv4 == ("203.0.113.5",)
        assert process.killed is True
