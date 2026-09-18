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
import os
import subprocess
from pathlib import Path

import pytest
from support import FakeProc

from pyntara import network_addresses
from pyntara.values import engine as engine_values
from pyntara.values import ssh_daemon_setup as ssh_daemon_values
from pyntara.values.ssh_daemon_setup import SshDirective

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
    assert records[1]["ssh"] == ("ssh -v -p 30222 fe80::b1e1:869:8e81:2526%enp87s0")


def test_the_zone_follows_the_address_and_not_its_family_name() -> None:
    # The zone index exists because an IPv6 link address is ambiguous, and
    # the address itself says whether it is one: a family renamed in the
    # engine table still gets the zone, and an IPv4 link address never
    # needs one.
    renamed = network_addresses.InterfaceAddress(
        address="fe80::1", family="six", interface="enp87s0", scope="link"
    )
    assert renamed.ssh_target("link") == "fe80::1%enp87s0"
    ipv4_link = network_addresses.InterfaceAddress(
        address="169.254.10.10", family="ipv4", interface="enp87s0", scope="link"
    )
    assert ipv4_link.ssh_target("link") == "169.254.10.10"
    unparseable = network_addresses.InterfaceAddress(
        address="not-an-address", family="ipv6", interface="enp87s0", scope="link"
    )
    assert unparseable.ssh_target("link") == "not-an-address"


def test_unexpected_document_contributes_nothing() -> None:
    # A document of an unexpected shape is not a crash: it carries no
    # address, and the caller reports the family as empty.
    assert network_addresses.parse_interface_addresses("not a list", "ipv4") == ()
    assert network_addresses.parse_interface_addresses([{"ifname": "lo"}], "ipv4") == ()


def test_main_prints_every_address_of_the_family(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        network_addresses,
        "run_command",
        lambda *args, **kwargs: FakeProc(0, IP_JSON),
    )
    assert network_addresses.main(["network_addresses", "4"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [record["address"] for record in records] == ["127.0.0.1", "10.10.0.1"]


def test_main_stdout_is_a_clean_json_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The module runs the ip query through run_command, whose command
    # tracing writes `run :` lines to stdout; the call must ask it to stay
    # quiet, otherwise the collector keeps the whole output as a string
    # instead of a JSON document.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_ip = bindir / "ip"
    fake_ip.write_text(
        f"#!/bin/sh\ncat <<'PYNTARA_IP_JSON'\n{IP_JSON}\nPYNTARA_IP_JSON\n",
        encoding="utf-8",
    )
    fake_ip.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ.get('PATH', '')}")
    assert network_addresses.main(["network_addresses", "4"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [record["address"] for record in records] == ["127.0.0.1", "10.10.0.1"]


def test_main_prints_nothing_for_an_absent_family(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A family the machine does not carry is not a failure: the module
    # reports empty, exactly like an ip command that prints nothing.
    monkeypatch.setattr(
        network_addresses,
        "run_command",
        lambda *args, **kwargs: FakeProc(0, json.dumps([])),
    )
    assert network_addresses.main(["network_addresses", "6"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_main_reports_a_failed_ip_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        network_addresses,
        "run_command",
        lambda *args, **kwargs: FakeProc(1, "", "ip: cannot find device"),
    )
    assert network_addresses.main(["network_addresses", "4"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot find device" in captured.err


def test_main_reports_a_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:

    def raise_timeout(*args: object, **kwargs: object) -> FakeProc:
        raise subprocess.TimeoutExpired(cmd="ip", timeout=1)

    monkeypatch.setattr(network_addresses, "run_command", raise_timeout)
    assert network_addresses.main(["network_addresses", "4"]) == 1
    assert "cannot read the interface addresses" in capsys.readouterr().err


def test_main_without_a_port_directive_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Without the sshd port there is no command to build, and the reason
    # must be visible instead of an empty address list.
    monkeypatch.setattr(
        ssh_daemon_values,
        "DIRECTIVES",
        (SshDirective(name="PermitRootLogin", value="no"),),
    )
    monkeypatch.setattr(
        network_addresses,
        "run_command",
        lambda *args, **kwargs: FakeProc(0, IP_JSON),
    )
    assert network_addresses.main(["network_addresses", "4"]) == 1
    assert "Port" in capsys.readouterr().err


def test_usage_requires_a_known_family(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A missing or unknown family is a usage error before any address is
    # read.
    assert network_addresses.main(["network_addresses"]) == 2
    assert network_addresses.main(["network_addresses", "8"]) == 2
    assert "usage" in capsys.readouterr().err


def test_the_address_vocabulary_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The query, the flag mapping, the iproute2 family names and the scope
    # value that counts as a link scope are declared values: other ones are
    # the argv the command runs and the family and zone the records carry.
    monkeypatch.setattr(engine_values, "INTERFACE_ADDRESSES_COMMAND", ("my-ip", "addr"))
    monkeypatch.setattr(engine_values, "ADDRESS_FAMILY_BY_FLAG", {"4": "ipv6"})
    monkeypatch.setattr(
        engine_values, "IPROUTE2_ADDRESS_FAMILY_NAMES", {"ipv6": "my-inet"}
    )
    monkeypatch.setattr(engine_values, "LINK_SCOPE_NAME", "my-link")
    document = [
        {
            "ifname": "enp87s0",
            "addr_info": [
                {
                    "family": "my-inet",
                    "local": "10.10.0.1",
                    "prefixlen": 24,
                    "scope": "global",
                },
                {
                    "family": "my-inet",
                    "local": "fe80::1",
                    "prefixlen": 64,
                    "scope": "my-link",
                },
            ],
        }
    ]
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        calls.append(list(command))
        return FakeProc(0, json.dumps(document))

    monkeypatch.setattr(network_addresses, "run_command", fake_run)
    assert network_addresses.main(["network_addresses", "4"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert calls == [["my-ip", "addr"]]
    assert [record["family"] for record in records] == ["ipv6", "ipv6"]
    assert records[0]["ssh"] == "ssh -v -p 30222 10.10.0.1"
    assert records[1]["ssh"] == "ssh -v -p 30222 fe80::1%enp87s0"
