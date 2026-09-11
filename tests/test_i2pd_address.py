"""Unit tests for the I2P address command (pyntara.i2pd_address).

The command runs on the target system and prints one JSON record with the
.b32.i2p tunnel address and the ssh command that reaches the SSH daemon
through the tunnel: the live keys file is the primary source, the saved
address file written by the i2pd_service_setup task is the fallback. The
tests exercise the branches through the main function with temporary
fixtures and capture stdout and stderr with capsys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from config_helpers import base_config, write_config
from support import i2pd_keys_b32_address, i2pd_keys_file_bytes

from pyntara import i2pd_address

SSH_PORT = 30222
SOCKS_PROXY = "127.0.0.1:4447"


def _config(tmp_path: Path, keys: Path, saved: Path) -> Path:
    """A config with the fixture paths and an sshd Port directive."""

    content = (
        base_config()
        .replace(
            'tunnel_keys_path = "/var/lib/i2pd/ssh.dat"',
            f'tunnel_keys_path = "{keys}"',
        )
        .replace(
            'address_file_path = "/var/lib/pyntara/i2pd_ssh_address"',
            f'address_file_path = "{saved}"',
        )
        .replace(
            "[ssh_client_setup]",
            "[[ssh_daemon_setup.directives]]\n"
            'name = "Port"\n'
            f'value = "{SSH_PORT}"\n'
            "[ssh_client_setup]",
        )
    )
    return write_config(tmp_path, content)


def test_record_from_keys(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    # The keys file decodes: the record carries the address, the sshd
    # port, the SOCKS proxy of the router and the ssh command that goes
    # through the tunnel, and nothing lands on stderr.
    keys = tmp_path / "ssh.dat"
    keys.write_bytes(i2pd_keys_file_bytes())
    config_path = _config(tmp_path, keys, tmp_path / "saved")
    assert i2pd_address.main(["i2pd_address", str(config_path)]) == 0
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


def test_record_falls_back_to_saved_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The keys file is missing but the saved address file exists: the
    # record carries the saved address and the reason as a note, so a
    # collector that keeps the document keeps the error.
    saved = tmp_path / "saved"
    saved.write_text(f"{i2pd_keys_b32_address()}\n", encoding="utf-8")
    config_path = _config(tmp_path, tmp_path / "missing.dat", saved)
    assert i2pd_address.main(["i2pd_address", str(config_path)]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["address"] == i2pd_keys_b32_address()
    assert "saved file" in record["note"]


def test_address_unavailable(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Neither the keys file nor the saved address file yields an address:
    # the command exits nonzero with an explanation on stderr and nothing
    # on stdout.
    config_path = _config(
        tmp_path, tmp_path / "missing.dat", tmp_path / "missing-saved"
    )
    assert i2pd_address.main(["i2pd_address", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_missing_config_key_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A config without the SOCKS proxy port cannot produce a complete
    # command: the reason names the missing key instead of printing a
    # command with an empty port.
    keys = tmp_path / "ssh.dat"
    keys.write_bytes(i2pd_keys_file_bytes())
    content = (
        base_config()
        .replace("socks_proxy_port = 4447\n", "")
        .replace(
            'tunnel_keys_path = "/var/lib/i2pd/ssh.dat"',
            f'tunnel_keys_path = "{keys}"',
        )
        .replace(
            "[ssh_client_setup]",
            "[[ssh_daemon_setup.directives]]\n"
            'name = "Port"\n'
            f'value = "{SSH_PORT}"\n'
            "[ssh_client_setup]",
        )
    )
    config_path = write_config(tmp_path, content)
    assert i2pd_address.main(["i2pd_address", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "socks_proxy_port" in captured.err


def test_usage_requires_the_config_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A wrong argument count is a usage error with a nonzero exit.
    assert i2pd_address.main(["i2pd_address"]) == 2
    assert "usage" in capsys.readouterr().err
