"""Unit tests for the task runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara import task_runner
from pyntara.config import CliToolsConfig, Config, EngineConfig
from pyntara.context import Context
from pyntara.models import TaskResult


def _ctx() -> Context:
    return Context(
        install_mode="minimal",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset(),
        task_data_root=Path("/tmp"),
        config=Config(
            engine=EngineConfig(
                task_data_root=Path("/tmp"),
                notice_timeout=7,
                command_timeout_seconds=1800,
                process_check_timeout_seconds=5,
            ),
            cli_tools=CliToolsConfig(
                packages=("mc", "htop"),
                package_status_timeout_seconds=30,
                package_install_retries=3,
            ),
        ),
    )


def test_run_tasks_reports_missing_implementation(monkeypatch: pytest.MonkeyPatch) -> None:
    # A task without a module is a skipped result, not a crash or a failure.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    results = task_runner.run_tasks(_ctx(), ["hostname"])
    assert len(results) == 1
    name, result = results[0]
    assert name == "hostname"
    assert result.success is False
    assert result.skipped is True
    assert "not implemented" in (result.message or "")


def test_run_tasks_calls_task_and_keeps_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_load(name: str) -> object:
        return lambda ctx: TaskResult(success=True, message="ok")

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    results = task_runner.run_tasks(_ctx(), ["hostname"])
    assert results == [("hostname", TaskResult(success=True, message="ok"))]


def test_run_tasks_catches_task_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ctx: Context) -> TaskResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(task_runner, "load_task", lambda name: boom)
    results = task_runner.run_tasks(_ctx(), ["hostname"])
    result = results[0][1]
    assert result.success is False
    assert result.error == "boom"


def test_run_tasks_reports_import_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(name: str) -> object:
        raise RuntimeError("import exploded")

    monkeypatch.setattr(task_runner, "load_task", broken)
    results = task_runner.run_tasks(_ctx(), ["hostname"])
    assert results[0][1].success is False
    assert "import failed" in (results[0][1].error or "")


def test_run_tasks_continues_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_load(name: str) -> object:
        if name == "a":
            return None
        return lambda ctx: TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    results = task_runner.run_tasks(_ctx(), ["a", "b"])
    assert len(results) == 2
    assert results[0][1].success is False
    assert results[0][1].skipped is True
    assert results[1][1].success is True
