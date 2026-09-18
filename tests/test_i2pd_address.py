"""Unit tests for the I2P address command (pyntara.i2pd_address).

The command runs on the target system and prints one JSON record with the
.b32.i2p tunnel address and the ssh command that reaches the SSH daemon
through the tunnel: the live keys file is the primary source, the saved
address file written by the i2pd_service_setup task is the fallback. The
tests exercise the branches through the main function with temporary
fixtures: the two paths are declared values, so the tests point them at
the fixtures, and capture stdout and stderr with capsys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support import i2pd_keys_b32_address, i2pd_keys_file_bytes

from pyntara import i2pd_address
from pyntara.values import engine as engine_values
from pyntara.values import i2pd_service_setup as values

SSH_PORT = 30222
SOCKS_PROXY = "127.0.0.1:4447"


def _point_at_the_fixtures(
    monkeypatch: pytest.MonkeyPatch, keys: Path, saved: Path
) -> None:
    """Point the declared paths at the temporary fixtures."""

    monkeypatch.setattr(values, "TUNNEL_KEYS_PATH", keys)
    monkeypatch.setattr(values, "ADDRESS_FILE_PATH", saved)


def test_record_from_keys(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The keys file decodes: the record carries the address, the sshd
    # port, the SOCKS proxy of the router and the ssh command that goes
    # through the tunnel, and nothing lands on stderr.
    keys = tmp_path / "ssh.dat"
    keys.write_bytes(i2pd_keys_file_bytes())
    _point_at_the_fixtures(monkeypatch, keys, tmp_path / "saved")
    assert i2pd_address.main(["i2pd_address"]) == 0
    captured = capsys.readouterr()
    address = i2pd_keys_b32_address()
    assert json.loads(captured.out) == {
        "channel": "i2p",
        "address": address,
        "port": SSH_PORT,
        "proxy": SOCKS_PROXY,
        "ssh": (
            f'ssh -v -p {SSH_PORT} -o ProxyCommand="nc -X 5 -x {SOCKS_PROXY} '
            f'%h %p" {address}'
        ),
    }
    assert captured.err == ""


def test_record_follows_the_declared_report_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The channel name of the record is a declared value and the field names
    # of the report come from the declared values, so a renamed channel or
    # field never needs a code change.
    keys = tmp_path / "ssh.dat"
    keys.write_bytes(i2pd_keys_file_bytes())
    _point_at_the_fixtures(monkeypatch, keys, tmp_path / "saved")
    record_keys = dict(engine_values.REPORT_RECORD_KEYS)
    record_keys["channel"] = "kind"
    record_keys["address"] = "target"
    monkeypatch.setattr(engine_values, "REPORT_RECORD_KEYS", record_keys)
    monkeypatch.setattr(values, "REPORT_CHANNEL_NAME", "anon")
    assert i2pd_address.main(["i2pd_address"]) == 0
    captured = capsys.readouterr()
    record = json.loads(captured.out)
    assert record["kind"] == "anon"
    assert record["target"] == i2pd_keys_b32_address()
    assert "channel" not in record
    assert captured.out.startswith('{\n  "kind":')


def test_record_falls_back_to_saved_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The keys file is missing but the saved address file exists: the
    # record carries the saved address and the reason as a note, so a
    # collector that keeps the document keeps the error.
    saved = tmp_path / "saved"
    saved.write_text(f"{i2pd_keys_b32_address()}\n", encoding="utf-8")
    _point_at_the_fixtures(monkeypatch, tmp_path / "missing.dat", saved)
    assert i2pd_address.main(["i2pd_address"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["address"] == i2pd_keys_b32_address()
    assert "saved file" in record["note"]


def test_address_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Neither the keys file nor the saved address file yields an address:
    # the command exits nonzero with an explanation on stderr and nothing
    # on stdout.
    _point_at_the_fixtures(
        monkeypatch, tmp_path / "missing.dat", tmp_path / "missing-saved"
    )
    assert i2pd_address.main(["i2pd_address"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_an_argument_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The command reads the declared values and takes no argument, so a
    # path on the command line is a usage error.
    assert i2pd_address.main(["i2pd_address", "/etc/pyntara/system.toml"]) == 2
    assert "usage" in capsys.readouterr().err
