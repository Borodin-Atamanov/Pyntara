"""Integration tests for journal forwarding through src/pyntara/logger.py.

The tests write into the real system journal through systemd-cat and read
the entries back through journalctl. conftest.py disables journal
forwarding globally with an empty PYNTARA_JOURNAL_IDENTIFIER, so each test
here sets its own unique identifier and restores the state through
monkeypatch. When journald is not available the integration tests skip,
the best-effort unit tests still run.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Iterator

import pytest

from pyntara import logger
from pyntara.models import TaskResult


def _close_journal_proc() -> None:
    """Terminate the shared systemd-cat process, if one is running.

    Closing stdin lets systemd-cat flush its buffered messages and exit
    normally; the timeout and kill are a fallback for a hung process.
    The module-level process must not leak between tests, because its
    journal identifier is fixed at creation time and would misroute the
    messages of the next test.
    """

    proc = logger._journal_proc
    if proc is None:
        return
    stdin = proc.stdin
    if stdin is not None:
        try:
            stdin.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    logger._journal_proc = None


@pytest.fixture(autouse=True)
def _reset_journal_proc() -> Iterator[None]:
    """Start every test with no journal process and leave none behind."""

    _close_journal_proc()
    yield
    _close_journal_proc()


def _read_journal(identifier: str) -> str:
    """Return all journal lines written under one identifier.

    Both the user and the system journal are read; one of them works
    depending on the privileges of the test process. A journalctl call
    that fails (no journald, no permission) is skipped.
    """

    chunks: list[str] = []
    for extra in (["--user"], []):
        try:
            result = subprocess.run(
                [
                    "journalctl",
                    *extra,
                    f"SYSLOG_IDENTIFIER={identifier}",
                    "--no-pager",
                    "-o",
                    "cat",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            chunks.append(result.stdout)
    return "\n".join(chunks)


def _wait_for(identifier: str, needle: str, timeout: float = 2.0) -> bool:
    """Poll the journal until the needle appears or the timeout expires."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in _read_journal(identifier):
            return True
        time.sleep(0.1)
    return False


def _wait_absent(identifier: str, needle: str, timeout: float = 1.5) -> bool:
    """Poll the journal and confirm the needle never appears."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in _read_journal(identifier):
            return False
        time.sleep(0.1)
    return True


@pytest.fixture(scope="module")
def journal_available() -> bool:
    """Probe the real journal once; integration tests skip when it fails."""

    identifier = f"probe-{uuid.uuid4().hex[:8]}"
    message = f"probe-message-{uuid.uuid4().hex[:8]}"
    try:
        result = subprocess.run(
            ["systemd-cat", "--identifier", identifier],
            input=f"{message}\n",
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return _wait_for(identifier, message)


def _new_identifier(prefix: str) -> str:
    return f"pyntara-{prefix}-{uuid.uuid4().hex[:8]}"


def _read_journal_priority(identifier: str, needle: str) -> str | None:
    """Return the syslog priority of the journal line containing the needle.

    The plain -o cat output carries no priority, so the JSON form is read
    and parsed per line; the first line that contains the needle reports
    its PRIORITY field, or None when the journal is not readable.
    """

    for extra in (["--user"], []):
        try:
            result = subprocess.run(
                [
                    "journalctl",
                    *extra,
                    f"SYSLOG_IDENTIFIER={identifier}",
                    "--no-pager",
                    "-o",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if needle in data.get("MESSAGE", ""):
                    return data.get("PRIORITY")
    return None


def test_log_progress_mirrors_message_without_timestamp(
    monkeypatch: pytest.MonkeyPatch, journal_available: bool
) -> None:
    # The journal line carries the task name and the message, no timestamp
    # and no ANSI codes; the task name is the calling module name.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("progress")
    marker = f"progress-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_progress(marker)
    assert _wait_for(identifier, f"test_logger: {marker}")


def test_log_task_start_mirrors_banner(monkeypatch: pytest.MonkeyPatch, journal_available: bool) -> None:
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("start")
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_task_start("sample_task")
    assert _wait_for(identifier, "starting task: sample_task")


def test_log_result_line_mirrors_outcome(monkeypatch: pytest.MonkeyPatch, journal_available: bool) -> None:
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("result")
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_result_line("cli_tools", TaskResult(success=True, message="all good"))
    assert _wait_for(identifier, "[done] cli_tools: all good")


def test_log_event_mirrors_status_line(monkeypatch: pytest.MonkeyPatch, journal_available: bool) -> None:
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("event")
    marker = f"event-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_event(marker)
    assert _wait_for(identifier, marker)


def test_log_event_default_priority_is_informational(
    monkeypatch: pytest.MonkeyPatch, journal_available: bool
) -> None:
    # Without an explicit priority the journal entry must be informational
    # (syslog level 6), the default for messages inside tasks.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("info-priority")
    marker = f"info-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_event(marker)
    assert _wait_for(identifier, marker)
    assert _read_journal_priority(identifier, marker) == "6"


def test_log_event_explicit_priority_reaches_the_journal(
    monkeypatch: pytest.MonkeyPatch, journal_available: bool
) -> None:
    # A serious error must be journaled at syslog level 3, passed as a
    # number, never as text in the message.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("error-priority")
    marker = f"error-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_event(marker, priority=3)
    assert _wait_for(identifier, marker)
    assert _read_journal_priority(identifier, marker) == "3"


def test_log_result_line_to_journal_false_skips_journal(
    monkeypatch: pytest.MonkeyPatch, journal_available: bool
) -> None:
    # to_journal=False prints to the console only; the journal must keep
    # the earlier line and never see the hidden one.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("quiet-result")
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_result_line("cli_tools", TaskResult(success=True, message="visible"))
    assert _wait_for(identifier, "[done] cli_tools: visible")
    logger.log_result_line("cli_tools", TaskResult(success=True, message="hidden"), to_journal=False)
    assert _wait_absent(identifier, "hidden")


def test_log_result_line_prints_warnings(
    monkeypatch: pytest.MonkeyPatch,
    journal_available: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Each warning of a completed result gets its own [warn] line on the
    # console and in the journal, after the [done] line.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("warn-result")
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_result_line(
        "cli_tools",
        TaskResult(
            success=True,
            message="done",
            warnings=("cannot apply hotkey", "no session"),
        ),
    )
    captured = capsys.readouterr()
    assert "[done] cli_tools: done" in captured.out
    assert "[warn] cli_tools: cannot apply hotkey" in captured.out
    assert "[warn] cli_tools: no session" in captured.out
    assert _wait_for(identifier, "[warn] cli_tools: cannot apply hotkey")


def test_log_result_line_shows_duration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger.log_result_line(
        "cli_tools",
        TaskResult(success=True, message="installed"),
        duration_seconds=12.345,
        to_journal=False,
    )
    captured = capsys.readouterr()
    assert "[done] cli_tools in 12.345s: installed" in captured.out


def test_log_result_line_skip_never_invents_not_implemented(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A skipped result without a message prints a bare [skip] line and must
    # never invent a reason: the old not implemented fallback misled users
    # into thinking a task is absent when it only skipped. A skip with a
    # message keeps its detail. to_journal=False keeps the test free of the
    # system journal.
    logger.log_result_line(
        "cli_tools", TaskResult(success=False, skipped=True), to_journal=False
    )
    captured = capsys.readouterr()
    assert "[skip] cli_tools" in captured.out
    assert "not implemented" not in captured.out
    logger.log_result_line(
        "cli_tools",
        TaskResult(success=False, skipped=True, message="skipped for a reason"),
        to_journal=False,
    )
    captured = capsys.readouterr()
    assert "[skip] cli_tools: skipped for a reason" in captured.out


def test_log_event_to_journal_false_skips_journal(
    monkeypatch: pytest.MonkeyPatch, journal_available: bool
) -> None:
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("quiet-event")
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", identifier)
    logger.log_event("visible event")
    assert _wait_for(identifier, "visible event")
    logger.log_event("hidden event", to_journal=False)
    assert _wait_absent(identifier, "hidden event")


def test_empty_identifier_disables_forwarding(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty identifier must short-circuit before any process is created;
    # the missing process is the deterministic proof that nothing was sent.
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", "")
    logger.log_event("must not reach the journal")
    assert logger._journal_proc is None


def test_default_identifier_is_pyntara_engine(
    monkeypatch: pytest.MonkeyPatch, journal_available: bool
) -> None:
    # Without the variable the identifier falls back to pyntara-engine;
    # the unique marker is found among the real engine entries.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    marker = f"default-{uuid.uuid4().hex[:8]}"
    monkeypatch.delenv("PYNTARA_JOURNAL_IDENTIFIER")
    logger.log_event(marker)
    assert _wait_for("pyntara-engine", marker)


def test_missing_systemd_cat_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Best effort: without systemd-cat the call does nothing and never raises.
    monkeypatch.setattr(logger.shutil, "which", lambda name: None)
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", "some-identifier")
    logger._send_to_journal("hello")
    assert logger._journal_proc is None


def test_popen_failure_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Best effort: a failed process spawn disables forwarding, not the run.
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("no systemd")

    monkeypatch.setattr(logger.subprocess, "Popen", boom)
    monkeypatch.setenv("PYNTARA_JOURNAL_IDENTIFIER", "some-identifier")
    logger._send_to_journal("hello")
    assert logger._journal_proc is None
