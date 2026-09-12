"""Unit tests for the public address command (pyntara.public_address_report).

The command runs on the target system as the public_address network
module of the System Metrics collector: it asks the configured echo
services for the address the machine appears under and prints one record
per address with the ssh command that reaches it. The shared detection
is replaced by a fake, so the tests assert the record shape and the exit
codes without a network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import base_config, write_config
from support import make_config

from pyntara import public_address_report
from pyntara.public_address import PublicAddresses


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


def _fake_detection(addresses: PublicAddresses):
    def detect(*args: object, **kwargs: object) -> PublicAddresses:
        return addresses

    return detect


def test_records_list_every_address_with_its_command() -> None:
    records = public_address_report.address_records(
        make_config(),
        PublicAddresses(ipv4=("190.55.165.52",), ipv6=("2a01:4f9:c012:8091::1",)),
        30222,
    )
    assert records == [
        {
            "address": "190.55.165.52",
            "family": "ipv4",
            "ssh": "ssh -v -p 30222 190.55.165.52",
        },
        {
            "address": "2a01:4f9:c012:8091::1",
            "family": "ipv6",
            "ssh": "ssh -v -p 30222 2a01:4f9:c012:8091::1",
        },
    ]


def test_a_silent_family_carries_its_reason() -> None:
    # A machine without a public IPv6 address is a normal machine, and
    # the report says which family did not answer instead of dropping it.
    records = public_address_report.address_records(
        make_config(), PublicAddresses(ipv4=("190.55.165.52",)), 30222
    )
    assert records[-1] == {
        "family": "ipv6",
        "reason": public_address_report.NO_ANSWER_REASON,
    }


def test_main_prints_the_records(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = _config(tmp_path)
    monkeypatch.setattr(
        public_address_report,
        "fetch_public_addresses",
        _fake_detection(PublicAddresses(ipv4=("190.55.165.52",))),
    )
    assert public_address_report.main(["public_address_report", str(config_path)]) == 0
    captured = capsys.readouterr()
    assert "190.55.165.52" in captured.out
    assert "ssh -v -p 30222 190.55.165.52" in captured.out
    assert captured.err == ""


def test_main_reports_a_silent_detection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # No service answering is an error, so the collector shows a failed
    # detection instead of an empty module.
    config_path = _config(tmp_path)
    monkeypatch.setattr(
        public_address_report,
        "fetch_public_addresses",
        _fake_detection(PublicAddresses()),
    )
    assert public_address_report.main(["public_address_report", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no echo service reported a public address" in captured.err


def test_main_without_a_port_directive_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path, base_config())
    monkeypatch.setattr(
        public_address_report,
        "fetch_public_addresses",
        _fake_detection(PublicAddresses(ipv4=("190.55.165.52",))),
    )
    assert public_address_report.main(["public_address_report", str(config_path)]) == 1
    assert "Port" in capsys.readouterr().err


def test_empty_service_list_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # An empty service list is a configuration gap, not a silent answer:
    # the reason says that nothing was configured.
    content = base_config().replace(
        'server_ip_services = ["https://api4.ipify.org", '
        '"https://ipv4.icanhazip.com", "https://v4.api.ipinfo.io/ip", '
        '"https://ipv4.myexternalip.com/raw", "https://4.ident.me", '
        '"https://check-host.net/ip"]',
        "server_ip_services = []",
    )
    config_path = write_config(tmp_path, content)
    assert public_address_report.main(
        ["public_address_report", str(config_path)]
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no echo service is configured" in captured.err


def test_usage_requires_the_config_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert public_address_report.main(["public_address_report"]) == 2
    assert "usage" in capsys.readouterr().err
