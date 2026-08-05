"""Unit tests for shared helpers in utils.py."""

from __future__ import annotations

import subprocess

import pytest

from pyntara.utils import run_command


def test_run_command_merges_extra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

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
    captured: dict[str, object] = {}

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
    captured: list[dict[str, object]] = []

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

