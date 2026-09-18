"""Unit tests for the public address command (pyntara.public_address_report).

The command runs on the target system as the public_address network
module of the System Metrics collector: it asks the configured echo
services for the address the machine appears under and prints one record
per address with the ssh command that reaches it. The shared detection
is replaced by a fake, so the tests assert the record shape and the exit
codes without a network.
"""

from __future__ import annotations

import pytest

from pyntara import public_address_report
from pyntara.public_address import PublicAddresses
from pyntara.values import engine as engine_values
from pyntara.values import ssh_daemon_setup as ssh_daemon_values
from pyntara.values import three_x_ui_xray_setup as panel_values
from pyntara.values.ssh_daemon_setup import SshDirective


def _use_the_ssh_port(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    """Declare the sshd Port directive the ssh command needs."""

    monkeypatch.setattr(
        ssh_daemon_values, "DIRECTIVES", (SshDirective(name="Port", value=port),)
    )


def _fake_detection(addresses: PublicAddresses):
    def detect(*args: object, **kwargs: object) -> PublicAddresses:
        return addresses

    return detect


def test_records_list_every_address_with_its_command() -> None:
    records = public_address_report.address_records(
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
    records = public_address_report.address_records( PublicAddresses(ipv4=("190.55.165.52",)), 30222
    )
    assert records[-1] == {
        "family": "ipv6",
        "reason": public_address_report.NO_ANSWER_REASON,
    }


def test_main_prints_the_records(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _use_the_ssh_port(monkeypatch, "30222")
    monkeypatch.setattr(
        public_address_report,
        "fetch_public_addresses",
        _fake_detection(PublicAddresses(ipv4=("190.55.165.52",))),
    )
    assert public_address_report.main(["public_address_report"]) == 0
    captured = capsys.readouterr()
    assert "190.55.165.52" in captured.out
    assert "ssh -v -p 30222 190.55.165.52" in captured.out
    assert captured.err == ""


def test_main_reports_a_silent_detection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No service answering is an error, so the collector shows a failed
    # detection instead of an empty module.
    _use_the_ssh_port(monkeypatch, "30222")
    monkeypatch.setattr(
        public_address_report,
        "fetch_public_addresses",
        _fake_detection(PublicAddresses()),
    )
    assert public_address_report.main(["public_address_report"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no echo service reported a public address" in captured.err


def test_main_without_a_port_directive_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        ssh_daemon_values,
        "DIRECTIVES",
        (SshDirective(name="PermitRootLogin", value="no"),),
    )
    monkeypatch.setattr(
        public_address_report,
        "fetch_public_addresses",
        _fake_detection(PublicAddresses(ipv4=("190.55.165.52",))),
    )
    assert public_address_report.main(["public_address_report"]) == 1
    assert "Port" in capsys.readouterr().err


def test_a_value_that_is_not_declared_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing is asked of a silent network when no echo service is declared:
    # the reason says that the value carries no service.
    monkeypatch.setattr(panel_values, "SERVER_IP_SERVICES", ())
    assert public_address_report.main(["public_address_report"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no echo service is declared" in captured.err


def test_the_family_words_and_the_reason_field_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The word the report writes into its family field and the name of the
    # field that carries the reason are declared values of the report shape:
    # another word and another name produce another document, and the reader
    # of the telemetry follows them.
    monkeypatch.setattr(
        engine_values, "REPORT_FAMILY_WORDS", {"ipv4": "v4", "ipv6": "v6"}
    )
    monkeypatch.setattr(
        engine_values,
        "REPORT_RECORD_KEYS",
        {**engine_values.REPORT_RECORD_KEYS, "reason": "why"},
    )
    records = public_address_report.address_records(
        PublicAddresses(ipv4=("190.55.165.52",)),
        30222,
    )
    assert records[0]["family"] == "v4"
    assert records[-1] == {
        "family": "v6",
        "why": public_address_report.NO_ANSWER_REASON,
    }


def test_both_families_of_the_model_carry_a_word() -> None:
    # The declared words name every family of the model, so a report never
    # carries a family field nobody can read; the adapter turns the words
    # back into the grade names of the telemetry.
    records = public_address_report.address_records( PublicAddresses(ipv4=(), ipv6=()), 30222
    )
    assert [record["family"] for record in records] == ["ipv4", "ipv6"]
    assert all(record["reason"] for record in records)


def test_usage_rejects_a_config_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert public_address_report.main(["public_address_report", "/etc/pyntara.toml"]) == 2
    assert "usage" in capsys.readouterr().err
