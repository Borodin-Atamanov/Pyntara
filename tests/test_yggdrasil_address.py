"""Unit tests for the yggdrasil address command (pyntara.yggdrasil_address).

The command runs on the target system and prints one JSON record with the
node self address and the ssh command that reaches the SSH daemon over
the overlay: the live admin socket query is the primary source, the saved
address file written by the yggdrasil_service_setup task is the fallback.
The command reads the declared values of the yggdrasil section, so the
tests point those values at temporary fixtures and capture stdout and
stderr with capsys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc

from pyntara import yggdrasil_address
from pyntara.values import ssh_daemon_setup as ssh_daemon_values
from pyntara.values import yggdrasil_service_setup as values

SELF_ADDRESS = "201:1234:5678:9abc:def0:1234:5678:9abc"
SSH_PORT = "30222"


def _fake_run(returncode: int, stdout: str = "", stderr: str = "") -> object:
    """A subprocess.run fake answering yggdrasilctl getSelf."""

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        assert command == list(values.SELF_ADDRESS_COMMAND)
        return _FakeProc(returncode, stdout, stderr)

    return fake_run


def _use_temporary_values(
    monkeypatch: pytest.MonkeyPatch, saved: Path, ssh_port: str | None = SSH_PORT
) -> None:
    """Point the declared values at the fixture address file and the port.

    ssh_port is the sshd Port directive; None leaves the declared
    directives empty, so the missing-port path is exercised.
    """

    monkeypatch.setattr(values, "ADDRESS_FILE_PATH", saved)
    directives = (
        ()
        if ssh_port is None
        else (ssh_daemon_values.SshDirective(name="Port", value=ssh_port),)
    )
    monkeypatch.setattr(ssh_daemon_values, "DIRECTIVES", directives)


def test_record_from_live_ctl(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # yggdrasilctl answers JSON with the self field: the record carries
    # the address, the sshd port and the direct ssh command, and nothing
    # lands on stderr.
    monkeypatch.setattr(
        yggdrasil_address.subprocess,
        "run",
        _fake_run(0, json.dumps({"address": SELF_ADDRESS, "subnet": "201::/64"})),
    )
    _use_temporary_values(monkeypatch, tmp_path / "saved")
    assert yggdrasil_address.main(["yggdrasil_address"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "channel": values.REPORT_CHANNEL_NAME,
        "address": SELF_ADDRESS,
        "port": int(SSH_PORT),
        "ssh": f"ssh -v -p {SSH_PORT} {SELF_ADDRESS}",
    }
    assert captured.err == ""


def test_fallback_to_saved_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The live query fails but the saved address file exists: the record
    # carries the saved address and the live reason as a note, so a
    # collector that keeps the document keeps the error.
    monkeypatch.setattr(yggdrasil_address.subprocess, "run", _fake_run(1, "", "boom"))
    saved = tmp_path / "saved"
    saved.write_text(f"{SELF_ADDRESS}\n", encoding="utf-8")
    _use_temporary_values(monkeypatch, saved)
    assert yggdrasil_address.main(["yggdrasil_address"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["address"] == SELF_ADDRESS
    assert "saved file" in record["note"]
    assert "boom" in record["note"]


def test_unparsable_output_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The live query exits 0 but the output is not JSON: the saved file
    # is used and the reason travels as a note.
    monkeypatch.setattr(yggdrasil_address.subprocess, "run", _fake_run(0, "not json"))
    saved = tmp_path / "saved"
    saved.write_text(f"{SELF_ADDRESS}\n", encoding="utf-8")
    _use_temporary_values(monkeypatch, saved)
    assert yggdrasil_address.main(["yggdrasil_address"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["address"] == SELF_ADDRESS
    assert "parse" in record["note"]


def test_address_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # Neither the live query nor the saved file yields an address: the
    # command exits nonzero, keeps the raw utility output in the stderr
    # reason and prints nothing on stdout.
    monkeypatch.setattr(
        yggdrasil_address.subprocess, "run", _fake_run(22, "", "ctl failed")
    )
    _use_temporary_values(monkeypatch, tmp_path / "missing-saved")
    assert yggdrasil_address.main(["yggdrasil_address"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ctl failed" in captured.err


def test_a_missing_ssh_port_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # The address is known but no Port directive is declared, so no ssh
    # command can be built and the reason names the missing port.
    monkeypatch.setattr(
        yggdrasil_address.subprocess,
        "run",
        _fake_run(0, json.dumps({"address": SELF_ADDRESS})),
    )
    _use_temporary_values(monkeypatch, tmp_path / "saved", ssh_port=None)
    assert yggdrasil_address.main(["yggdrasil_address"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Port" in captured.err


def test_usage_takes_no_argument(capsys: pytest.CaptureFixture[str]) -> None:
    # The command reads declared values, so an extra argument is a usage
    # error with a nonzero exit.
    assert yggdrasil_address.main(["yggdrasil_address", "extra"]) == 2
    assert "usage" in capsys.readouterr().err
