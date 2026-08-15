"""Unit tests for the yggdrasil address command (pyntara.yggdrasil_address).

The command runs on the target system and prints the node self address
on stdout: the live admin socket query is the primary source, the saved
address file written by the yggdrasil_service_setup task is the
fallback. The tests exercise the branches through the main function with
a fake subprocess runner and temporary fixtures and capture stdout and
stderr with capsys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc

from pyntara import yggdrasil_address

SELF_ADDRESS = "201:1234:5678:9abc:def0:1234:5678:9abc"


def _fake_run(returncode: int, stdout: str = "", stderr: str = "") -> object:
    """A subprocess.run fake answering yggdrasilctl getSelf."""

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        assert command == ["yggdrasilctl", "getSelf"]
        return _FakeProc(returncode, stdout, stderr)

    return fake_run


def test_address_from_live_ctl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # yggdrasilctl answers JSON with the self field: the command prints
    # the address on stdout and nothing on stderr.
    monkeypatch.setattr(
        yggdrasil_address.subprocess,
        "run",
        _fake_run(0, json.dumps({"self": SELF_ADDRESS, "subnet": "201::/64"})),
    )
    saved = tmp_path / "saved"
    assert yggdrasil_address.main(["yggdrasil_address", str(saved)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == SELF_ADDRESS
    assert captured.err == ""


def test_fallback_to_saved_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The live query fails but the saved address file exists: the command
    # prints the saved address and the reason on the following stdout
    # line, so a collector that takes the stdout keeps the error.
    monkeypatch.setattr(
        yggdrasil_address.subprocess, "run", _fake_run(1, "", "boom")
    )
    saved = tmp_path / "saved"
    saved.write_text(f"{SELF_ADDRESS}\n", encoding="utf-8")
    assert yggdrasil_address.main(["yggdrasil_address", str(saved)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == SELF_ADDRESS
    assert "saved file" in captured.out
    assert captured.err == ""


def test_unparsable_output_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The live query exits 0 but the output is not JSON: the saved file
    # is used and the reason goes to stdout.
    monkeypatch.setattr(
        yggdrasil_address.subprocess, "run", _fake_run(0, "not json")
    )
    saved = tmp_path / "saved"
    saved.write_text(f"{SELF_ADDRESS}\n", encoding="utf-8")
    assert yggdrasil_address.main(["yggdrasil_address", str(saved)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == SELF_ADDRESS
    assert "parse" in captured.out
    assert captured.err == ""


def test_address_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Neither the live query nor the saved file yields an address: the
    # command exits nonzero, keeps the raw utility output in the stderr
    # reason and prints nothing on stdout.
    monkeypatch.setattr(
        yggdrasil_address.subprocess, "run", _fake_run(22, "", "ctl failed")
    )
    saved = tmp_path / "missing-saved"
    assert yggdrasil_address.main(["yggdrasil_address", str(saved)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ctl failed" in captured.err


def test_usage_requires_one_argument(capsys: pytest.CaptureFixture[str]) -> None:
    # A wrong argument count is a usage error with a nonzero exit.
    assert yggdrasil_address.main(["yggdrasil_address"]) == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err
