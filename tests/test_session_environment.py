"""Unit tests for the desktop session environment helpers.

The helpers read the environment of a user's session manager through the
configured command, keep only the configured variables and hand them to the
rest of the run, so the tests cover the parse, the completeness rule and the
export without ever calling a real session manager.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest

from pyntara.utils import (
    export_session_environment,
    parse_session_environment,
    session_bus_address,
    session_environment,
    session_environment_command,
    session_environment_value,
    user_session_environment,
)

# Values of the test config, repeated here so a change of the shipped config
# cannot silently change what these tests prove.
COMMAND = ("systemctl", "--machine", "{username}@.host", "--user", "show-environment")
KEYS = (
    "DBUS_SESSION_BUS_ADDRESS",
    "WAYLAND_DISPLAY",
    "DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
)
BUS_KEY = "DBUS_SESSION_BUS_ADDRESS"
DISPLAY_KEYS = ("WAYLAND_DISPLAY", "DISPLAY")

# The text a session manager prints for a live desktop session, including the
# variables the run must never take and one escaped value.
SESSION_TEXT = (
    "HOME=/home/i\n"
    "PATH=/home/i/.local/bin:/usr/bin\n"
    "SSH_AUTH_SOCK=/run/user/1000/gcr/ssh\n"
    "DEBUGINFOD_URLS=$'https://debuginfod.ubuntu.com '\n"
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\n"
    "WAYLAND_DISPLAY=wayland-0\n"
    "DISPLAY=:0\n"
    "XAUTHORITY=/run/user/1000/xauth_haxlCi\n"
    "XDG_RUNTIME_DIR=/run/user/1000\n"
)


def _fake_session_command(
    monkeypatch: pytest.MonkeyPatch, stdout: str, returncode: int = 0
) -> dict[str, Any]:
    """Replace the command runner and capture the command it was given."""

    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return captured


def test_session_environment_command_fills_the_user() -> None:
    assert session_environment_command("i", COMMAND) == [
        "systemctl",
        "--machine",
        "i@.host",
        "--user",
        "show-environment",
    ]


def test_parse_session_environment_keeps_the_configured_keys_only() -> None:
    environment = parse_session_environment(SESSION_TEXT, KEYS)
    assert environment == {
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "WAYLAND_DISPLAY": "wayland-0",
        "DISPLAY": ":0",
        "XAUTHORITY": "/run/user/1000/xauth_haxlCi",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }


def test_parse_session_environment_never_takes_root_variables() -> None:
    # HOME, PATH, SSH_AUTH_SOCK and the locale belong to the root process of
    # the run; taking them would move the home directory of the run and make
    # root use the agent of the desktop user.
    environment = parse_session_environment(SESSION_TEXT, KEYS)
    assert "HOME" not in environment
    assert "PATH" not in environment
    assert "SSH_AUTH_SOCK" not in environment


def test_parse_session_environment_skips_an_escaped_value() -> None:
    # A value the session manager printed as an ANSI-C string is left alone
    # instead of being taken with its escapes.
    text = "DEBUGINFOD_URLS=$'https://example.test/path with space'\n"
    assert parse_session_environment(text, ("DEBUGINFOD_URLS",)) == {}


def test_session_environment_value_unquotes_a_quoted_value() -> None:
    assert session_environment_value('"a value"') == "a value"


def test_session_environment_value_refuses_a_backslash() -> None:
    assert session_environment_value("a\\tb") is None


def test_user_session_environment_runs_the_configured_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _fake_session_command(monkeypatch, SESSION_TEXT)
    environment = user_session_environment(
        "i", command_template=COMMAND, keys=KEYS, timeout=5
    )
    assert environment["WAYLAND_DISPLAY"] == "wayland-0"
    assert captured["command"] == [
        "systemctl",
        "--machine",
        "i@.host",
        "--user",
        "show-environment",
    ]


def test_user_session_environment_without_a_user_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _fake_session_command(monkeypatch, SESSION_TEXT)
    assert (
        user_session_environment("", command_template=COMMAND, keys=KEYS, timeout=5)
        == {}
    )
    assert captured == {}


def test_user_session_environment_reports_a_failed_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A session manager that does not answer is not a crash: the caller gets
    # an empty dict and the reason is written to the log.
    logged: list[str] = []
    monkeypatch.setattr("pyntara.utils.logger.log_progress", logged.append)
    _fake_session_command(monkeypatch, "", returncode=1)
    assert (
        user_session_environment("i", command_template=COMMAND, keys=KEYS, timeout=5)
        == {}
    )
    assert logged


def test_session_bus_address_needs_no_display_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A DBus client of a task reaches the session services with the bus alone.
    _fake_session_command(monkeypatch, SESSION_TEXT)
    assert (
        session_bus_address(
            "i",
            command_template=COMMAND,
            keys=KEYS,
            bus_key=BUS_KEY,
            timeout=5,
        )
        == "unix:path=/run/user/1000/bus"
    )


def test_session_environment_drops_a_session_without_a_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[str] = []
    monkeypatch.setattr("pyntara.utils.logger.log_progress", logged.append)
    _fake_session_command(monkeypatch, "WAYLAND_DISPLAY=wayland-0\nDISPLAY=:0\n")
    assert (
        session_environment(
            "i",
            command_template=COMMAND,
            keys=KEYS,
            bus_key=BUS_KEY,
            display_keys=DISPLAY_KEYS,
            timeout=5,
        )
        == {}
    )
    assert logged


def test_session_environment_drops_a_session_without_a_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The bus alone cannot carry a GUI tool: without a display variable Qt
    # picks a platform plugin that aborts before anything is applied.
    logged: list[str] = []
    monkeypatch.setattr("pyntara.utils.logger.log_progress", logged.append)
    _fake_session_command(
        monkeypatch, "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\n"
    )
    assert (
        session_environment(
            "i",
            command_template=COMMAND,
            keys=KEYS,
            bus_key=BUS_KEY,
            display_keys=DISPLAY_KEYS,
            timeout=5,
        )
        == {}
    )
    assert logged


def test_session_environment_returns_a_complete_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_session_command(monkeypatch, SESSION_TEXT)
    environment = session_environment(
        "i",
        command_template=COMMAND,
        keys=KEYS,
        bus_key=BUS_KEY,
        display_keys=DISPLAY_KEYS,
        timeout=5,
    )
    assert environment["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert environment["WAYLAND_DISPLAY"] == "wayland-0"


def test_export_session_environment_reaches_the_target() -> None:
    target: dict[str, str] = {"PATH": "/usr/bin"}
    exported = export_session_environment(
        {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, target
    )
    assert exported == ("WAYLAND_DISPLAY", "DISPLAY")
    assert target["WAYLAND_DISPLAY"] == "wayland-0"
    assert target["DISPLAY"] == ":0"
    assert target["PATH"] == "/usr/bin"


def test_export_session_environment_defaults_to_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The export of the run reaches every child process, because it goes into
    # the environment of the process itself.
    monkeypatch.delenv("PYNTARA_SESSION_PROBE", raising=False)
    exported = export_session_environment({"PYNTARA_SESSION_PROBE": "yes"})
    assert exported == ("PYNTARA_SESSION_PROBE",)
    assert os.environ["PYNTARA_SESSION_PROBE"] == "yes"
