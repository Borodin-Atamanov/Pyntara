"""Unit tests for the run command: environment resolution and exit codes."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from pyntara import task_catalog, task_runner
from pyntara.models import TaskResult
from pyntara.pyntara import app

runner = CliRunner()


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "PYNTARA_INSTALL_MODE",
        "PYNTARA_TASKS",
        "PYNTARA_VAULT_PASSWORD",
        "PYNTARA_VAULT_SOURCE",
        "PYNTARA_FORCE_TASKS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_run_requires_install_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "PYNTARA_INSTALL_MODE is not set" in result.output


def test_run_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "fancy")
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "unknown install mode" in result.output


def test_run_warns_and_continues_on_unknown_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unknown task name is not fatal: the engine shows an error notice,
    # pauses, then continues without the unknown name (simplified
    # architecture section 2).
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_TASKS", "nope")
    monkeypatch.setenv("PYNTARA_NOTICE_TIMEOUT", "0")
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "unknown task names in PYNTARA_TASKS" in result.output
    assert "All 0 tasks finished" in result.output


def test_run_pauses_on_invalid_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    # The notice must stay visible: the engine sleeps for the configured
    # timeout before continuing.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_TASKS", "nope")
    monkeypatch.setenv("PYNTARA_NOTICE_TIMEOUT", "1")
    slept: list[float] = []
    monkeypatch.setattr("pyntara.pyntara.time.sleep", lambda seconds: slept.append(seconds))
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert slept == [1.0]


def test_run_uses_mode_defaults_and_reports_not_implemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no task modules yet, every default task must be reported as failed
    # and the command must exit nonzero: provisioning did not happen.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    for name in task_catalog.default_tasks("minimal"):
        assert f"[failed] {name}" in result.output


def test_run_resolves_selected_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    # PYNTARA_TASKS selects tasks; dependencies are resolved inside the engine.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "server")
    monkeypatch.setenv("PYNTARA_TASKS", "proxy_tunnel")
    result = runner.invoke(app, [])
    assert "Tasks: proxy_server proxy_tunnel" in result.output


def test_run_warns_and_continues_on_unknown_force_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo in the force list shows a notice and the run continues with the
    # remaining tasks.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "hostnam")
    monkeypatch.setenv("PYNTARA_NOTICE_TIMEOUT", "0")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: hostnam" in result.output


def test_run_warns_and_continues_on_force_tasks_outside_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Forcing a task that would never run is a notice, not a stop: the run
    # continues without the invalid entry.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "apps")
    monkeypatch.setenv("PYNTARA_NOTICE_TIMEOUT", "0")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: apps" in result.output
    assert "Force:" not in result.output


def test_run_reports_force_tasks_in_the_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A valid force list is reported and does not change the task set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "hostname users")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Force: hostname users" in result.output


def test_run_reports_success_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # When every task succeeds, the run reports the count and exits 0.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True, message="done")

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    expected = len(task_catalog.default_tasks("minimal"))
    assert f"All {expected} tasks finished" in result.output
    assert "[done] users" in result.output
