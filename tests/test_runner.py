"""Unit tests for the task runner."""

from __future__ import annotations

import pytest
from support import make_config, make_context

from pyntara import task_runner
from pyntara.context import Context
from pyntara.models import TaskResult


def _ctx() -> Context:
    # task_start_delay_seconds is zeroed so an implemented task does not
    # sleep half a second before running.
    return make_context(config=make_config(task_start_delay_seconds=0))


def test_run_tasks_reports_missing_implementation(monkeypatch: pytest.MonkeyPatch) -> None:
    # A task without a module is a skipped result, not a crash or a failure.
    # The example name is an implemented task so the test stays meaningful
    # if future catalog entries change.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    results = task_runner.run_tasks(_ctx(), ["cli_tools"])
    assert len(results) == 1
    name, result = results[0]
    assert name == "cli_tools"
    assert result.success is False
    assert result.skipped is True
    assert "not implemented" in (result.message or "")


def test_run_tasks_calls_task_and_keeps_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_load(name: str) -> object:
        return lambda ctx: TaskResult(success=True, message="ok")

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    results = task_runner.run_tasks(_ctx(), ["cli_tools"])
    assert results == [("cli_tools", TaskResult(success=True, message="ok"))]


def test_run_tasks_catches_task_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    # A raising task becomes a completed result with the reason in warnings,
    # so a broken task never stops the run.
    def boom(ctx: Context) -> TaskResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(task_runner, "load_task", lambda name: boom)
    results = task_runner.run_tasks(_ctx(), ["cli_tools"])
    result = results[0][1]
    assert result.success is True
    assert result.warnings == ("boom",)
    assert result.message == "completed with warnings"


def test_run_tasks_reports_import_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    # A broken import becomes a completed result with the reason in warnings.
    def broken(name: str) -> object:
        raise RuntimeError("import exploded")

    monkeypatch.setattr(task_runner, "load_task", broken)
    results = task_runner.run_tasks(_ctx(), ["cli_tools"])
    result = results[0][1]
    assert result.success is True
    assert any("import failed" in warning for warning in result.warnings)


def test_run_tasks_converts_task_failure_to_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A task that reports success=False for a recoverable failure is
    # converted into a completed result carrying the reason as a warning.
    def fake_load(name: str) -> object:
        return lambda ctx: TaskResult(success=False, error="cannot apply hotkey")

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    results = task_runner.run_tasks(_ctx(), ["cli_tools"])
    result = results[0][1]
    assert result.success is True
    assert result.warnings == ("cannot apply hotkey",)
    assert result.message == "completed with warnings"


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
