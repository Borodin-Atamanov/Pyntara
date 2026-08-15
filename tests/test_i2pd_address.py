"""Unit tests for the I2P address command (pyntara.i2pd_address).

The command runs on the target system and prints the .b32.i2p tunnel
address on stdout: the live keys file is the primary source, the saved
address file written by the i2pd_service_setup task is the fallback.
The tests exercise the three branches through the main function with
temporary fixtures and capture stdout and stderr with capsys.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import i2pd_keys_b32_address, i2pd_keys_file_bytes

from pyntara import i2pd_address


def test_address_from_keys(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    # The keys file decodes: the command prints the address on stdout
    # and nothing on stderr.
    keys = tmp_path / "ssh.dat"
    keys.write_bytes(i2pd_keys_file_bytes())
    saved = tmp_path / "saved"
    assert i2pd_address.main(["i2pd_address", str(keys), str(saved)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == i2pd_keys_b32_address()
    assert captured.err == ""


def test_address_falls_back_to_saved_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The keys file is missing but the saved address file exists: the
    # command prints the saved address and the reason on the following
    # stdout line, so a collector that takes the stdout keeps the error
    # instead of losing it.
    keys = tmp_path / "missing.dat"
    saved = tmp_path / "saved"
    saved.write_text(f"{i2pd_keys_b32_address()}\n", encoding="utf-8")
    assert i2pd_address.main(["i2pd_address", str(keys), str(saved)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == i2pd_keys_b32_address()
    assert "saved file" in captured.out
    assert captured.err == ""


def test_address_unavailable(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Neither the keys file nor the saved address file yields an address:
    # the command exits nonzero with an explanation on stderr and nothing
    # on stdout.
    keys = tmp_path / "missing.dat"
    saved = tmp_path / "missing-saved"
    assert i2pd_address.main(["i2pd_address", str(keys), str(saved)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_usage_requires_two_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A wrong argument count is a usage error with a nonzero exit.
    assert i2pd_address.main(["i2pd_address"]) == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err
