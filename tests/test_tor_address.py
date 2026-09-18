"""Unit tests for the Tor address command (pyntara.tor_address).

The command runs on the target system and prints one JSON record with the
SSH onion address and the ssh command that reaches the SSH daemon through
the onion service: the live hostname file is the primary source, the saved
address file written by the tor_setup task is the fallback. The command
reads the declared values of the tor_setup section and takes no argument,
so the tests point the two paths at the fixture tree and capture stdout and
stderr with capsys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyntara import tor_address
from pyntara.values import tor_setup as values

ADDRESS = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion"
ONION_PORT = values.ONION_SSH_PORT
SOCKS_PROXY = f"127.0.0.1:{values.SOCKS_PORT}"


def _point_at_the_fixtures(
    monkeypatch: pytest.MonkeyPatch, hidden_service_dir: Path, saved: Path
) -> None:
    """Point the declared paths at the temporary fixtures."""

    monkeypatch.setattr(values, "HIDDEN_SERVICE_DIR", hidden_service_dir)
    monkeypatch.setattr(values, "ADDRESS_FILE_PATH", saved)


def test_record_from_hostname(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The hostname file decodes: the record carries the address, the
    # virtual port of the onion service, the SOCKS proxy of the daemon and
    # the ssh command that goes through it. The trailing newline of the
    # file is trimmed and nothing lands on stderr.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    (hidden / values.HOSTNAME_FILE_NAME).write_text(f"{ADDRESS}\n", encoding="utf-8")
    _point_at_the_fixtures(monkeypatch, hidden, tmp_path / "saved")
    assert tor_address.main(["tor_address"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "channel": "tor",
        "address": ADDRESS,
        "port": ONION_PORT,
        "proxy": SOCKS_PROXY,
        "ssh": (
            f'ssh -v -p {ONION_PORT} -o ProxyCommand="nc -X 5 -x {SOCKS_PROXY} '
            f'%h %p" {ADDRESS}'
        ),
    }
    assert captured.err == ""


def test_record_follows_the_declared_report_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The channel name of the record is a declared value, so a renamed
    # channel never needs a code change.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    (hidden / values.HOSTNAME_FILE_NAME).write_text(f"{ADDRESS}\n", encoding="utf-8")
    _point_at_the_fixtures(monkeypatch, hidden, tmp_path / "saved")
    monkeypatch.setattr(values, "REPORT_CHANNEL_NAME", "anon")
    assert tor_address.main(["tor_address"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["channel"] == "anon"
    assert record["address"] == ADDRESS


def test_record_falls_back_to_saved_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The hostname file is missing but the saved address file exists: the
    # record carries the saved address and the reason as a note, so a
    # collector that keeps the document keeps the error.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    saved = tmp_path / "saved"
    saved.write_text(f"{ADDRESS}\n", encoding="utf-8")
    _point_at_the_fixtures(monkeypatch, hidden, saved)
    assert tor_address.main(["tor_address"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["address"] == ADDRESS
    assert "saved file" in record["note"]


def test_address_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Neither the hostname file nor the saved address file yields an
    # address: the command exits nonzero with an explanation on stderr and
    # nothing on stdout.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    _point_at_the_fixtures(monkeypatch, hidden, tmp_path / "missing-saved")
    assert tor_address.main(["tor_address"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_an_argument_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The command reads the declared values and takes no argument, so a
    # path on the command line is a usage error.
    assert tor_address.main(["tor_address", "/etc/pyntara/config.toml"]) == 2
    assert "usage" in capsys.readouterr().err
