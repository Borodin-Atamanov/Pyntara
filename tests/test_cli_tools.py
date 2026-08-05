"""Unit tests for the cli_tools task.

All external resources (dpkg-query, apt-get) are mocked via monkeypatch;
the tests never touch the real system (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pyntara import task_catalog
from pyntara.config import CliToolsConfig, Config, EngineConfig
from pyntara.context import Context
from pyntara.tasks import cli_tools

# Package set used by the tests; mirrors the real config but stays small.
TEST_PACKAGES = ("mc", "htop", "hollywood")


def _ctx() -> Context:
    return Context(
        install_mode="minimal",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset(),
        task_data_root=Path("/tmp"),
        config=Config(
            engine=EngineConfig(task_data_root=Path("/tmp"), notice_timeout=7),
            cli_tools=CliToolsConfig(packages=TEST_PACKAGES),
        ),
    )


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _install_fake(
    monkeypatch: pytest.MonkeyPatch, *, installed: set[str]
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query answers from the installed set, every other command succeeds
    and is recorded.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            if command[-1] in installed:
                return _FakeProc(0, "install ok installed\n")
            return _FakeProc(1, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_cli_tools_is_in_every_mode_default_set() -> None:
    for mode in task_catalog.MODES:
        assert "cli_tools" in task_catalog.default_tasks(mode)


def test_cli_tools_has_no_dependencies() -> None:
    task_def = task_catalog.by_name("cli_tools")
    assert task_def is not None
    assert task_def.depends == ()


def test_all_installed_skips_apt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_missing_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only mc is missing; apt must install exactly that package.
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES) - {"mc"})
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert "mc" in (result.message or "")
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", "mc"]]


def test_config_files_leftover_counts_as_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A package in "deinstall ok config-files" state is not fully installed
    # and must be reinstalled.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(0, "deinstall ok config-files\n")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(_ctx())
    assert result.changed is True
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", *TEST_PACKAGES]]


def test_apt_failure_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        raise subprocess.CalledProcessError(100, command)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(_ctx())
    assert result.success is False
    assert result.changed is False
    assert result.error


def test_apt_hang_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        raise subprocess.TimeoutExpired(command, timeout=1800)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(_ctx())
    assert result.success is False
    assert result.error


def test_optimistic_retry_refreshes_index(monkeypatch: pytest.MonkeyPatch) -> None:
    # The first install fails on a stale index; apt-get update runs and the
    # retry succeeds (bootstrap contract section 2 strategy).
    calls: list[list[str]] = []
    first_install = True

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal first_install
        calls.append(list(command))
        if command[0] == "dpkg-query":
            return _FakeProc(1, "")
        if command[0] == "apt-get" and command[1] == "install" and first_install:
            first_install = False
            raise subprocess.CalledProcessError(100, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = cli_tools.task(_ctx())
    assert result.success is True
    assert result.changed is True
    updates = [call for call in calls if call[0] == "apt-get" and call[1] == "update"]
    assert len(updates) == 1


def test_force_mode_keeps_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force mode reruns the task but does not change the outcome when the
    # target state is already reached.
    ctx = Context(
        install_mode="minimal",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset({"cli_tools"}),
        task_data_root=Path("/tmp"),
        config=Config(
            engine=EngineConfig(task_data_root=Path("/tmp"), notice_timeout=7),
            cli_tools=CliToolsConfig(packages=TEST_PACKAGES),
        ),
    )
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = cli_tools.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert not any(call[0] == "apt-get" for call in calls)
