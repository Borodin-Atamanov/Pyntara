"""Unit tests for the task runner."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from support import make_context

from pyntara import task_runner
from pyntara.context import Context
from pyntara.models import TaskResult


def _ctx() -> Context:
    # task_start_delay_seconds is zeroed so an implemented task does not
    # sleep half a second before running.
    return make_context()


def test_run_tasks_reports_missing_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A task without a module is a skipped result, not a crash or a failure.
    # The example name is an implemented task so the test stays meaningful
    # if future catalog entries change.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    results, stop_reason = task_runner.run_tasks(_ctx(), ["cli_tools_lite_setup"])
    assert stop_reason is None
    assert len(results) == 1
    name, result = results[0]
    assert name == "cli_tools_lite_setup"
    assert result.success is False
    assert result.skipped is True
    assert "not implemented" in (result.message or "")


def test_run_tasks_calls_task_and_keeps_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_load(name: str) -> object:
        return lambda ctx: TaskResult(success=True, message="ok")

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    results, _ = task_runner.run_tasks(_ctx(), ["cli_tools_lite_setup"])
    assert results == [("cli_tools_lite_setup", TaskResult(success=True, message="ok"))]


def test_run_tasks_hands_each_task_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The catalog is the single source of truth for task names: the runner
    # gives each task the name it looked up, so no module writes its own.
    seen: list[str] = []

    def fake_load(name: str) -> object:
        def fake_task(ctx: Context) -> TaskResult:
            seen.append(ctx.task_name)
            return TaskResult(success=True)

        return fake_task

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    task_runner.run_tasks(_ctx(), ["cli_tools_lite_setup", "hostname"])
    assert seen == ["cli_tools_lite_setup", "hostname"]

def test_run_tasks_catches_task_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    # A raising task becomes a completed result with the reason in warnings,
    # so a broken task never stops the run.
    def boom(ctx: Context) -> TaskResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(task_runner, "load_task", lambda name: boom)
    results, _ = task_runner.run_tasks(_ctx(), ["cli_tools_lite_setup"])
    result = results[0][1]
    assert result.success is True
    assert result.warnings == ("boom",)
    assert result.message == "completed with warnings"


def test_run_tasks_reports_import_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    # A broken import becomes a completed result with the reason in warnings.
    def broken(name: str) -> object:
        raise RuntimeError("import exploded")

    monkeypatch.setattr(task_runner, "load_task", broken)
    results, _ = task_runner.run_tasks(_ctx(), ["cli_tools_lite_setup"])
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
    results, _ = task_runner.run_tasks(_ctx(), ["cli_tools_lite_setup"])
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
    results, _ = task_runner.run_tasks(_ctx(), ["a", "b"])
    assert len(results) == 2
    assert results[0][1].success is False
    assert results[0][1].skipped is True
    assert results[1][1].success is True


def test_run_tasks_reports_task_duration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_load(name: str) -> object:
        return lambda ctx: TaskResult(success=True, message="ok")

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    task_runner.run_tasks(_ctx(), ["cli_tools_lite_setup"])
    captured = capsys.readouterr().out
    assert re.search(r"\[done\] cli_tools_lite_setup in \d+\.\d{3}s: ok", captured)


def test_run_tasks_frees_the_package_cache_in_economy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What the run downloaded is deleted as it goes: the package download cache
    # is freed after every task, so nothing piles up between two tasks.
    calls: list[float] = []

    def fake_cleanup(timeout: float) -> str | None:
        calls.append(timeout)
        return None

    def fake_load(name: str) -> object:
        return lambda ctx: TaskResult(success=True)

    monkeypatch.setattr(task_runner, "free_package_download_cache", fake_cleanup)
    monkeypatch.setattr(task_runner, "load_task", fake_load)
    _, stop_reason = task_runner.run_tasks(_ctx(), ["a", "b"])
    assert stop_reason is None
    assert len(calls) == 2


def test_run_tasks_keeps_the_package_cache_when_the_run_keeps_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A run that asked to keep the downloads runs no cleanup at all, so a
    # repeated run reuses what the machine already has.
    calls: list[float] = []

    def fake_cleanup(timeout: float) -> str | None:
        calls.append(timeout)
        return None

    def fake_load(name: str) -> object:
        return lambda ctx: TaskResult(success=True)

    monkeypatch.setattr(task_runner, "free_package_download_cache", fake_cleanup)
    monkeypatch.setattr(task_runner, "load_task", fake_load)
    task_runner.run_tasks(
        make_context(delete_packages_after_install=False), ["cli_tools_lite_setup"]
    )
    assert calls == []


def test_run_tasks_reports_a_failed_cache_cleanup_and_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A cleanup that cannot run is reported and changes nothing: the run
    # continues with the remaining tasks.
    def fake_load(name: str) -> object:
        return lambda ctx: TaskResult(success=True)

    monkeypatch.setattr(
        task_runner,
        "free_package_download_cache",
        lambda _timeout: "the package download cache was not freed: boom",
    )
    monkeypatch.setattr(task_runner, "load_task", fake_load)
    results, stop_reason = task_runner.run_tasks(_ctx(), ["cli_tools_lite_setup"])
    assert stop_reason is None
    assert len(results) == 1
    assert "the package download cache was not freed: boom" in capsys.readouterr().out


def test_run_tasks_stops_before_a_task_without_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reserve is a stop, not a warning: the remaining tasks stay unstarted
    # and the reason names the task and how many tasks were not run, so a
    # machine that is nearly full is never filled by the run itself.
    seen: list[str] = []

    def fake_load(name: str) -> object:
        def fake_task(ctx: Context) -> TaskResult:
            seen.append(name)
            return TaskResult(success=True)

        return fake_task

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    monkeypatch.setattr(
        task_runner,
        "disk_shortage_message",
        lambda: "free space 4 MB is below the reserve 10 MB",
    )
    results, stop_reason = task_runner.run_tasks(_ctx(), ["a", "b"])
    assert seen == []
    assert results == []
    assert stop_reason is not None
    assert "before a" in stop_reason
    assert "2 of 2 tasks were not started" in stop_reason


def test_run_tasks_stops_after_the_last_task_that_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The free space is asked before every task, so the tasks that still fit run
    # and only the first task without room stays unstarted.
    seen: list[str] = []
    answers: list[str | None] = [
        None,
        "free space 4 MB is below the reserve 10 MB",
    ]

    def fake_load(name: str) -> object:
        def fake_task(ctx: Context) -> TaskResult:
            seen.append(name)
            return TaskResult(success=True)

        return fake_task

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    monkeypatch.setattr(
        task_runner,
        "disk_shortage_message",
        lambda: answers.pop(0) if answers else None,
    )
    results, stop_reason = task_runner.run_tasks(_ctx(), ["a", "b", "c"])
    assert seen == ["a"]
    assert [name for name, _ in results] == ["a"]
    assert stop_reason is not None
    assert "before b" in stop_reason
    assert "2 of 3 tasks were not started" in stop_reason


def test_task_modules_report_findings_in_warnings() -> None:
    """No task module builds an error result.

    The runner converts an error result into a completed one carrying the
    reason in warnings, so a run never stops there; the rule of the task
    contract (architecture contract, Task contract) is that a task
    reports a step it could not perform in warnings and completes, and
    this check keeps every module in that shape.
    """

    tasks_dir = Path(__file__).resolve().parents[1] / "src" / "pyntara" / "tasks"
    offenders = [
        path.name
        for path in sorted(tasks_dir.glob("*.py"))
        if "success=False" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
