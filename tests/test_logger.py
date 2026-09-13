"""Integration tests for journal forwarding through src/pyntara/logger.py.

The tests write into the real system journal through systemd-cat and read
the entries back through journalctl. conftest.py switches journal forwarding
off globally, so each test here configures the journal with its own unique
identifier and the autouse fixture restores the off state afterwards. When
journald is not available the integration tests skip, the best-effort unit
tests still run.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace

import pytest
from support import make_config

from pyntara import logger
from pyntara.models import TaskResult


def _use_identifier(identifier: str) -> None:
    """Write the journal with one identifier for the current test.

    The logger receives the whole engine table, exactly as the composition
    root and the deployed services hand it over: the journal command of the
    table stays the configured one and only the identifier is replaced, so
    the test exercises the real rendering path.
    """

    logger.configure_journal(make_config(journal_identifier=identifier).engine)


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
    """Start every test with no journal process and leave none behind.

    The configured engine is dropped as well, so the identifier of one test
    never routes the messages of the next one.
    """

    _close_journal_proc()
    yield
    _close_journal_proc()
    logger.configure_journal(None)


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


def _journal_with_marker(identifier: str, marker: str) -> str:
    """Return the journal text once the marker line has arrived.

    A line that must not reach the journal is proved absent by ordering,
    not by waiting out a window: the marker is sent after the call under
    test through the same reused systemd-cat process, so its arrival in
    the journal means every earlier line of that process was delivered,
    and a needle missing from the returned text was never sent. A fixed
    absence window costs its whole length on every run, while the round
    trip through journald takes milliseconds.
    """

    logger.log_event(marker)
    assert _wait_for(identifier, marker), (
        f"the marker line {marker!r} never reached the journal"
    )
    return _read_journal(identifier)


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
    journal_available: bool,
) -> None:
    # The journal line carries the task name and the message, no timestamp
    # and no ANSI codes; the task name is the calling module name.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("progress")
    marker = f"progress-{uuid.uuid4().hex[:8]}"
    _use_identifier(identifier)
    logger.log_progress(marker)
    assert _wait_for(identifier, f"test_logger: {marker}")


def test_log_task_start_mirrors_banner(journal_available: bool) -> None:
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("start")
    _use_identifier(identifier)
    logger.log_task_start("sample_task")
    assert _wait_for(identifier, "starting task: sample_task")


def test_log_result_line_mirrors_outcome(journal_available: bool) -> None:
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("result")
    _use_identifier(identifier)
    logger.log_result_line("cli_tools", TaskResult(success=True, message="all good"))
    assert _wait_for(identifier, "[done] cli_tools: all good")


def test_log_event_mirrors_status_line(journal_available: bool) -> None:
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("event")
    marker = f"event-{uuid.uuid4().hex[:8]}"
    _use_identifier(identifier)
    logger.log_event(marker)
    assert _wait_for(identifier, marker)


def test_log_event_default_priority_is_informational(
    journal_available: bool,
) -> None:
    # Without an explicit priority the journal entry must be informational
    # (syslog level 6), the default for messages inside tasks.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("info-priority")
    marker = f"info-{uuid.uuid4().hex[:8]}"
    _use_identifier(identifier)
    logger.log_event(marker)
    assert _wait_for(identifier, marker)
    assert _read_journal_priority(identifier, marker) == "6"


def test_log_event_explicit_priority_reaches_the_journal(
    journal_available: bool,
) -> None:
    # A serious error must be journaled at syslog level 3, passed as a
    # number, never as text in the message.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("error-priority")
    marker = f"error-{uuid.uuid4().hex[:8]}"
    _use_identifier(identifier)
    logger.log_event(marker, priority=3)
    assert _wait_for(identifier, marker)
    assert _read_journal_priority(identifier, marker) == "3"


def test_log_result_line_to_journal_false_skips_journal(
    journal_available: bool,
) -> None:
    # to_journal=False prints to the console only; the journal must keep
    # the earlier line and never see the hidden one. The marker line goes
    # through the same shared systemd-cat process after the hidden call,
    # so its arrival proves that an earlier hidden line would be there.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("quiet-result")
    _use_identifier(identifier)
    logger.log_result_line("cli_tools", TaskResult(success=True, message="visible"))
    assert _wait_for(identifier, "[done] cli_tools: visible")
    logger.log_result_line("cli_tools", TaskResult(success=True, message="hidden"), to_journal=False)
    journal = _journal_with_marker(identifier, "result path finished")
    assert "hidden" not in journal


def test_log_result_line_prints_warnings(
    journal_available: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Each warning of a completed result gets its own [warn] line on the
    # console and in the journal, after the [done] line.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("warn-result")
    _use_identifier(identifier)
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
    journal_available: bool,
) -> None:
    # The event form of the same rule: the marker line travels through the
    # shared process after the hidden event, so its arrival proves the
    # hidden event was never sent.
    if not journal_available:
        pytest.skip("systemd journal is not available")
    identifier = _new_identifier("quiet-event")
    _use_identifier(identifier)
    logger.log_event("visible event")
    assert _wait_for(identifier, "visible event")
    logger.log_event("hidden event", to_journal=False)
    journal = _journal_with_marker(identifier, "event path finished")
    assert "hidden event" not in journal


def test_unconfigured_journal_forwards_nothing() -> None:
    # Without a configured engine nothing is sent: the composition root and
    # the deployed services hand the table over before the first message,
    # so an unconfigured logger means the caller is not the engine and no
    # name may be invented for it. The missing systemd-cat process is the
    # deterministic proof that nothing was sent.
    logger.configure_journal(None)
    logger.log_event("must not reach the journal")
    assert logger._journal_proc is None


def test_empty_journal_command_forwards_nothing() -> None:
    # An engine table that names no journal command cannot start a process;
    # the console and the install log keep working as before. The missing
    # process is the deterministic proof that nothing was sent.
    engine = make_config().engine
    logger.configure_journal(replace(engine, journal_command=()))
    logger.log_event("must not reach the journal")
    assert logger._journal_proc is None


def test_missing_systemd_cat_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Best effort: without systemd-cat the call does nothing and never raises.
    monkeypatch.setattr(logger.shutil, "which", lambda name: None)
    _use_identifier("some-identifier")
    logger._send_to_journal("hello")
    assert logger._journal_proc is None


def test_popen_failure_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Best effort: a failed process spawn disables forwarding, not the run.
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("no systemd")

    monkeypatch.setattr(logger.subprocess, "Popen", boom)
    _use_identifier("some-identifier")
    logger._send_to_journal("hello")
    assert logger._journal_proc is None
