"""Unit tests for the Tor address command (pyntara.tor_address).

The command runs on the target system and prints the SSH onion address
on stdout: the live hostname file is the primary source, the saved
address file written by the tor_setup task is the fallback. The tests
exercise the three branches through the main function with temporary
fixtures and capture stdout and stderr with capsys.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara import tor_address

ADDRESS = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion"


def test_address_from_hostname(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The hostname file decodes: the command prints the address on stdout
    # and nothing on stderr. The trailing newline of the file is trimmed.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    (hidden / "hostname").write_text(f"{ADDRESS}\n", encoding="utf-8")
    saved = tmp_path / "saved"
    assert tor_address.main(["tor_address", str(hidden), str(saved)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == ADDRESS
    assert captured.err == ""


def test_address_falls_back_to_saved_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The hostname file is missing but the saved address file exists: the
    # command prints the saved address and the reason on the following
    # stdout line, so a collector that takes the stdout keeps the error
    # instead of losing it.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    saved = tmp_path / "saved"
    saved.write_text(f"{ADDRESS}\n", encoding="utf-8")
    assert tor_address.main(["tor_address", str(hidden), str(saved)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == ADDRESS
    assert "saved file" in captured.out
    assert captured.err == ""


def test_address_unavailable(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Neither the hostname file nor the saved address file yields an
    # address: the command exits nonzero with an explanation on stderr and
    # nothing on stdout.
    hidden = tmp_path / "tor" / "ssh"
    hidden.mkdir(parents=True)
    saved = tmp_path / "missing-saved"
    assert tor_address.main(["tor_address", str(hidden), str(saved)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_usage_requires_two_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A wrong argument count is a usage error with a nonzero exit.
    assert tor_address.main(["tor_address"]) == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err
