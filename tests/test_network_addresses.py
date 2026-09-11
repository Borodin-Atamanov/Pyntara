"""Unit tests for the local address command (pyntara.network_addresses).

The command runs on the target system as the ipv4 and ipv6 network
modules of the System Metrics collector: it reads the addresses of one
family from iproute2 in JSON form and prints one record per address with
the ssh command that reaches it. The tests exercise the parsing and the
branches through the main function with temporary fixtures and capture
stdout and stderr with capsys.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from config_helpers import base_config, write_config
from support import FakeProc

from pyntara import network_addresses

IP_DOCUMENT = [
    {
        "ifname": "lo",
        "addr_info": [
            {"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"},
            {"family": "inet6", "local": "::1", "prefixlen": 128, "scope": "host"},
        ],
    },
    {
        "ifname": "enp87s0",
        "addr_info": [
            {
                "family": "inet",
                "local": "10.10.0.1",
                "prefixlen": 24,
                "scope": "global",
            },
            {
                "family": "inet6",
                "local": "fe80::b1e1:869:8e81:2526",
                "prefixlen": 64,
                "scope": "link",
            },
        ],
    },
]

IP_JSON = json.dumps(IP_DOCUMENT)


def _config(tmp_path: Path) -> Path:
    """A config whose sshd Port directive exists, as the target has one."""

    content = base_config().replace(
        "[ssh_client_setup]",
        '[[ssh_daemon_setup.directives]]\n'
        'name = "Port"\n'
        'value = "30222"\n'
        "[ssh_client_setup]",
    )
    return write_config(tmp_path, content)


def test_address_records_carry_the_ssh_command() -> None:
    # Every address of the family becomes a record with its interface,
    # its scope and the ssh command that connects to it.
    assert network_addresses.address_records(IP_DOCUMENT, "ipv4", 30222) == [
        {
            "address": "127.0.0.1",
            "family": "ipv4",
            "interface": "lo",
            "scope": "host",
            "ssh": "ssh -v -p 30222 127.0.0.1",
        },
        {
            "address": "10.10.0.1",
            "family": "ipv4",
            "interface": "enp87s0",
            "scope": "global",
            "ssh": "ssh -v -p 30222 10.10.0.1",
        },
    ]


def test_link_scope_address_carries_its_zone_in_the_command() -> None:
    # An IPv6 link scope address is ambiguous without its interface, so
    # the address field stays plain and the ssh target carries the zone.
    records = network_addresses.address_records(IP_DOCUMENT, "ipv6", 30222)
    assert [record["address"] for record in records] == [
        "::1",
        "fe80::b1e1:869:8e81:2526",
    ]
    assert records[1]["ssh"] == (
        "ssh -v -p 30222 fe80::b1e1:869:8e81:2526%enp87s0"
    )


def test_unexpected_document_contributes_nothing() -> None:
    # A document of an unexpected shape is not a crash: it carries no
    # address, and the caller reports the family as empty.
    assert network_addresses.parse_interface_addresses("not a list", "ipv4") == ()
    assert network_addresses.parse_interface_addresses([{"ifname": "lo"}], "ipv4") == ()


def test_main_prints_every_address_of_the_family(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config_path = _config(tmp_path)
    monkeypatch.setattr(
        network_addresses,
        "run_command",
        lambda *args, **kwargs: FakeProc(0, IP_JSON),
    )
    assert network_addresses.main(["network_addresses", str(config_path), "4"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [record["address"] for record in records] == ["127.0.0.1", "10.10.0.1"]


def test_main_prints_nothing_for_an_absent_family(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A family the machine does not carry is not a failure: the module
    # reports empty, exactly like an ip command that prints nothing.
    config_path = _config(tmp_path)
    monkeypatch.setattr(
        network_addresses,
        "run_command",
        lambda *args, **kwargs: FakeProc(0, json.dumps([])),
    )
    assert network_addresses.main(["network_addresses", str(config_path), "6"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_main_reports_a_failed_ip_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config_path = _config(tmp_path)
    monkeypatch.setattr(
        network_addresses,
        "run_command",
        lambda *args, **kwargs: FakeProc(1, "", "ip: cannot find device"),
    )
    assert network_addresses.main(["network_addresses", str(config_path), "4"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot find device" in captured.err


def test_main_reports_a_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config_path = _config(tmp_path)

    def raise_timeout(*args: object, **kwargs: object) -> FakeProc:
        raise subprocess.TimeoutExpired(cmd="ip", timeout=1)

    monkeypatch.setattr(network_addresses, "run_command", raise_timeout)
    assert network_addresses.main(["network_addresses", str(config_path), "4"]) == 1
    assert "cannot read the interface addresses" in capsys.readouterr().err


def test_main_without_a_port_directive_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Without the sshd port there is no command to build, and the reason
    # must be visible instead of an empty address list.
    config_path = write_config(tmp_path, base_config())
    monkeypatch.setattr(
        network_addresses,
        "run_command",
        lambda *args, **kwargs: FakeProc(0, IP_JSON),
    )
    assert network_addresses.main(["network_addresses", str(config_path), "4"]) == 1
    assert "Port" in capsys.readouterr().err


def test_usage_requires_a_known_family(capsys: pytest.CaptureFixture[str]) -> None:
    assert network_addresses.main(["network_addresses"]) == 2
    assert network_addresses.main(["network_addresses", "config.toml", "8"]) == 2
    assert "usage" in capsys.readouterr().err
