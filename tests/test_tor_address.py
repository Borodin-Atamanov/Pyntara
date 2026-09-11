"""Unit tests for the Tor address command (pyntara.tor_address).

The command runs on the target system and prints one JSON record with the
SSH onion address and the ssh command that reaches the SSH daemon
through the onion service: the live hostname file is the primary source,
the saved address file written by the tor_setup task is the fallback. The
tests exercise the branches through the main function with temporary
fixtures and capture stdout and stderr with capsys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from config_helpers import base_config, write_config

from pyntara import tor_address

ADDRESS = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion"
ONION_PORT = 22
SOCKS_PROXY = "127.0.0.1:9050"


def _config(tmp_path: Path, hidden_service_dir: Path, saved: Path) -> Path:
    """A config with the fixture paths of the hidden service."""

    content = (
        base_config()
        .replace(
            'hidden_service_dir = "/var/lib/tor/ssh"',
            f'hidden_service_dir = "{hidden_service_dir}"',
        )
        .replace(
            'address_file_path = "/var/lib/pyntara/tor_ssh_address"',
            f'address_file_path = "{saved}"',
        )
    )
    return write_config(tmp_path, content)


def test_record_from_hostname(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The hostname file decodes: the record carries the address, the
    # virtual port of the onion service, the SOCKS proxy of the daemon and
    # the ssh command that goes through it. The trailing newline of the
    # file is trimmed and nothing lands on stderr.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    (hidden / "hostname").write_text(f"{ADDRESS}\n", encoding="utf-8")
    config_path = _config(tmp_path, hidden, tmp_path / "saved")
    assert tor_address.main(["tor_address", str(config_path)]) == 0
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


def test_record_falls_back_to_saved_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The hostname file is missing but the saved address file exists: the
    # record carries the saved address and the reason as a note, so a
    # collector that keeps the document keeps the error.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    saved = tmp_path / "saved"
    saved.write_text(f"{ADDRESS}\n", encoding="utf-8")
    config_path = _config(tmp_path, hidden, saved)
    assert tor_address.main(["tor_address", str(config_path)]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["address"] == ADDRESS
    assert "saved file" in record["note"]


def test_address_unavailable(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Neither the hostname file nor the saved address file yields an
    # address: the command exits nonzero with an explanation on stderr and
    # nothing on stdout.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    config_path = _config(tmp_path, hidden, tmp_path / "missing-saved")
    assert tor_address.main(["tor_address", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_usage_requires_the_config_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A wrong argument count is a usage error with a nonzero exit.
    assert tor_address.main(["tor_address"]) == 2
    assert "usage" in capsys.readouterr().err
