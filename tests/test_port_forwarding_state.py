"""Unit tests for the port-forwarding state command.

The command runs on the target system as the port_forwarding network
module of the System Metrics collector: it prints one JSON record per
server and forwarded local port from the state file written by the
auto_port_forwarding service, and every record carries the ssh command
that reaches this machine through that server and port. The tests
exercise the branches through the main function with temporary fixtures
and capture stdout and stderr with capsys. The command reads its values
from the values package, so no config takes part.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyntara import port_forwarding_state
from pyntara.values import port_forwarding_setup as values

LOCAL_PORT = 30222


def _point_the_state_file_at_the_fixture(
    monkeypatch: pytest.MonkeyPatch, state: Path
) -> None:
    """Give the command the state file of the test."""

    monkeypatch.setattr(values, "STATE_FILE_PATH", state)


def _write_state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "169.58.51.98": {str(LOCAL_PORT): 46132},
                "2001:db8::1": {str(LOCAL_PORT): 48012},
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_records_carry_the_ssh_command_of_every_forward() -> None:
    # The remote port is the port a person connects to on the server, and
    # the local port is the sshd port the reverse tunnel delivers to.
    assert port_forwarding_state.state_records(
        {"https://vpn.example.com": {str(LOCAL_PORT): 46132}},
    ) == [
        {
            "channel": "port_forwarding",
            "server": "vpn.example.com",
            "local_port": LOCAL_PORT,
            "remote_port": 46132,
            "ssh": "ssh -v -p 46132 vpn.example.com",
        }
    ]


def test_malformed_entries_are_skipped() -> None:
    # One unreadable entry must not hide the rest of the forwarding state.
    records = port_forwarding_state.state_records(
        {
            "169.58.51.98": {str(LOCAL_PORT): "not a port"},
            "broken": "not a table",
            "2001:db8::1": {"5000": 48012},
        },
    )
    assert records == [
        {
            "channel": "port_forwarding",
            "server": "2001:db8::1",
            "local_port": 5000,
            "remote_port": 48012,
            "ssh": "ssh -v -p 48012 2001:db8::1",
        }
    ]


def test_prints_records(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # An existing state file prints one record per server and local port.
    state = tmp_path / "state.json"
    _write_state(state)
    _point_the_state_file_at_the_fixture(monkeypatch, state)
    assert port_forwarding_state.main(["port_forwarding_state"]) == 0
    captured = capsys.readouterr()
    records = json.loads(captured.out)
    assert [record["ssh"] for record in records] == [
        "ssh -v -p 46132 169.58.51.98",
        "ssh -v -p 48012 2001:db8::1",
    ]
    assert captured.err == ""


def test_missing_file_prints_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # A missing state file means no forwarding is configured: the command
    # prints nothing and exits 0, so the collector module reports empty
    # instead of an error.
    _point_the_state_file_at_the_fixture(monkeypatch, tmp_path / "missing.json")
    assert port_forwarding_state.main(["port_forwarding_state"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_corrupt_file_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # A corrupt state file is reported on stderr with a nonzero exit, so
    # the collector shows the failure instead of dropping it silently.
    state = tmp_path / "state.json"
    state.write_text("not json", encoding="utf-8")
    _point_the_state_file_at_the_fixture(monkeypatch, state)
    assert port_forwarding_state.main(["port_forwarding_state"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_a_wrong_argument_count_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The command takes no argument at all: the values it reads live in the
    # package, so a caller that still passes a config path is told the
    # usage instead of being ignored.
    assert port_forwarding_state.main(["port_forwarding_state", "extra"]) == 2
    assert capsys.readouterr().err != ""
