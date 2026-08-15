"""Unit tests for shared helpers in utils.py."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from support import FakeProc as _FakeProc

from pyntara.utils import (
    run_command,
    service_is_active,
    service_is_enabled,
    trim_whitespace,
)


def test_run_command_merges_extra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=1800, extra_env={"DEBIAN_FRONTEND": "noninteractive"})
    env = captured["kwargs"]["env"]
    assert isinstance(env, dict)
    assert env["DEBIAN_FRONTEND"] == "noninteractive"


def test_run_command_applies_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=42)
    assert captured["kwargs"]["timeout"] == 42


def test_run_command_streams_by_default_and_captures_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=1800)
    run_command(["true"], timeout=1800, capture=True)
    assert "capture_output" not in captured[0] or captured[0]["capture_output"] is False
    assert captured[1]["capture_output"] is True


def test_run_command_feeds_stdin_when_input_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["cat"], timeout=1800, input="payload")
    assert captured["kwargs"]["input"] == "payload"


def test_run_command_omits_input_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=1800)
    # Without input the subprocess default (None) is used, so the
    # explicit argument never carries a payload.
    assert captured["kwargs"].get("input") is None


@pytest.mark.parametrize(
    "output,expected",
    [
        ("enabled\n", True),
        ("disabled\n", False),
        ("", False),
    ],
)
def test_service_is_enabled_matches_only_enabled(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: bool
) -> None:
    # Only the exact "enabled" state means the service starts at boot.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        assert command == ["systemctl", "is-enabled", "svc.service"]
        assert kwargs["check"] is False
        return _FakeProc(0 if output == "enabled\n" else 1, output)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    assert service_is_enabled("svc.service", timeout=5) is expected


@pytest.mark.parametrize(
    "output,expected",
    [
        ("active\n", True),
        ("inactive\n", False),
        ("failed\n", False),
    ],
)
def test_service_is_active_matches_only_active(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: bool
) -> None:
    # Only the exact "active" state means the service is running.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        assert command == ["systemctl", "is-active", "svc.service"]
        assert kwargs["check"] is False
        return _FakeProc(0 if output == "active\n" else 1, output)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    assert service_is_active("svc.service", timeout=5) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", ""),
        ("   \n\t\n  ", ""),
        ("  text  ", "text"),
        ("\n\t text \n", "text"),
        ("line one\nline two\n", "line one\nline two"),
        ("  \nfirst\n\nlast\n  ", "first\n\nlast"),
    ],
)
def test_trim_whitespace_removes_edges_only(text: str, expected: str) -> None:
    # Leading and trailing whitespace is removed; everything between the
    # edges, including internal newlines, is preserved.
    assert trim_whitespace(text) == expected

