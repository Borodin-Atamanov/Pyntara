"""Unit tests for the port-forwarding state command.

The command runs on the target system and prints the assigned remote
ports from the state file written by the auto_port_forwarding service;
the System Metrics collector runs it as the port_forwarding network
module. The tests exercise the branches through the main function with
temporary fixtures and capture stdout and stderr with capsys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyntara import port_forwarding_state


def _write_state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {"169.58.51.98": {"30222": 46132}, "2001:db8::1": {"30222": 48012}},
            indent=2,
        ),
        encoding="utf-8",
    )


def test_prints_ports(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    # An existing state file prints one line per server and local port.
    state = tmp_path / "state.json"
    _write_state(state)
    assert port_forwarding_state.main(["port_forwarding_state", str(state)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "169.58.51.98: local 30222 to remote 46132",
        "2001:db8::1: local 30222 to remote 48012",
    ]
    assert captured.err == ""


def test_missing_file_prints_nothing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    # A missing state file means no forwarding is configured: the command
    # prints nothing and exits 0, so the collector module reports empty
    # instead of an error.
    state = tmp_path / "missing.json"
    assert port_forwarding_state.main(["port_forwarding_state", str(state)]) == 0
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
    assert port_forwarding_state.main(["port_forwarding_state", str(state)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_missing_argument_is_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert port_forwarding_state.main(["port_forwarding_state"]) == 2
    captured = capsys.readouterr()
    assert captured.err != ""
