"""Unit tests for the port-forwarding state command.

The command runs on the target system as the port_forwarding network
module of the System Metrics collector: it prints one JSON record per
server and forwarded local port from the state file written by the
auto_port_forwarding service, and every record carries the ssh command
that reaches this machine through that server and port. The tests
exercise the branches through the main function with temporary fixtures
and capture stdout and stderr with capsys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from config_helpers import base_config, write_config

from pyntara import port_forwarding_state

LOCAL_PORT = 30222


def _config(tmp_path: Path, state: Path) -> Path:
    """A config whose state file path is the fixture."""

    content = base_config().replace(
        'state_file_path = "/var/lib/pyntara/port_forwarding_state.json"',
        f'state_file_path = "{state}"',
    )
    return write_config(tmp_path, content)


def _write_state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {"169.58.51.98": {str(LOCAL_PORT): 46132}, "2001:db8::1": {str(LOCAL_PORT): 48012}},
            indent=2,
        ),
        encoding="utf-8",
    )


def test_records_carry_the_ssh_command_of_every_forward() -> None:
    # The remote port is the port a person connects to on the server, and
    # the local port is the sshd port the reverse tunnel delivers to.
    assert port_forwarding_state.state_records(
        {"https://vpn.example.com": {str(LOCAL_PORT): 46132}}
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
        }
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


def test_prints_records(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    # An existing state file prints one record per server and local port.
    state = tmp_path / "state.json"
    _write_state(state)
    config_path = _config(tmp_path, state)
    assert port_forwarding_state.main(["port_forwarding_state", str(config_path)]) == 0
    captured = capsys.readouterr()
    records = json.loads(captured.out)
    assert [record["ssh"] for record in records] == [
        "ssh -v -p 46132 169.58.51.98",
        "ssh -v -p 48012 2001:db8::1",
    ]
    assert captured.err == ""


def test_missing_file_prints_nothing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A missing state file means no forwarding is configured: the command
    # prints nothing and exits 0, so the collector module reports empty
    # instead of an error.
    config_path = _config(tmp_path, tmp_path / "missing.json")
    assert port_forwarding_state.main(["port_forwarding_state", str(config_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_corrupt_file_is_an_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A corrupt state file is reported on stderr with a nonzero exit, so
    # the collector shows the failure instead of dropping it silently.
    state = tmp_path / "state.json"
    state.write_text("not json", encoding="utf-8")
    config_path = _config(tmp_path, state)
    assert port_forwarding_state.main(["port_forwarding_state", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_missing_state_path_key_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A config without the state file path cannot find the forwarding
    # state: the reason names the missing key instead of reporting an
    # empty module.
    content = base_config().replace(
        'state_file_path = "/var/lib/pyntara/port_forwarding_state.json"\n',
        "",
    )
    config_path = write_config(tmp_path, content)
    assert port_forwarding_state.main(
        ["port_forwarding_state", str(config_path)]
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "state_file_path" in captured.err


def test_missing_argument_is_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert port_forwarding_state.main(["port_forwarding_state"]) == 2
    assert capsys.readouterr().err != ""
