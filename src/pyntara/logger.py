"""Central logging helpers for the engine.

Every own message of the engine flows through this module: task progress
lines, task banners, result lines and status events. Each helper writes to
the console exactly like the code it replaces (project rules, Task progress output)
and duplicates the message into the system journal through systemd-cat.
The journal receives plain text without the console timestamp, because the
journal stamps its own time, and without ANSI color codes. Subprocess
output streams straight from run_command and never passes through here.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

import typer

from pyntara.models import TaskResult

# ANSI color codes from typer.secho must never reach the journal.
_ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*m")

# Persistent systemd-cat process; None until the first journal message.
_journal_proc: subprocess.Popen[str] | None = None

# Monotonic time of the previous progress line; presentation state only.
# Shared across tasks, so a timestamp is printed at most once per second
# for the whole run and bursts of lines stay compact.
_last_log_time = 0.0


def _write_to_shared_journal(text: str, identifier: str) -> None:
    """Write one line through the reused systemd-cat process, best effort.

    The shared process writes informational entries (syslog level 6),
    used when a message carries the explicit informational priority.
    A missing executable or a failed write never stops the run: without a
    journal the console and the install log keep working as before.
    """

    global _journal_proc
    if _journal_proc is None or _journal_proc.poll() is not None:
        systemd_cat = shutil.which("systemd-cat")
        if systemd_cat is None:
            return
        try:
            _journal_proc = subprocess.Popen(
                [systemd_cat, "--identifier", identifier],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            _journal_proc = None
            return
    stdin = _journal_proc.stdin
    if stdin is None:
        return
    try:
        stdin.write(text)
        stdin.flush()
    except OSError:
        # The journal pipe broke; stop forwarding for the rest of the run.
        _journal_proc = None


def _write_to_priority_journal(text: str, identifier: str, priority: int) -> None:
    """Write one line through a short-lived systemd-cat process, best effort.

    systemd-cat fixes the priority at process start, so a message with a
    non-default priority cannot go through the shared process: a dedicated
    process is spawned with the numeric --priority option and closed after
    the single line. The priority is passed as a number, never embedded in
    the message text. A missing executable or a failed write never stops
    the run.
    """

    systemd_cat = shutil.which("systemd-cat")
    if systemd_cat is None:
        return
    try:
        proc = subprocess.Popen(
            [
                systemd_cat,
                "--identifier",
                identifier,
                "--priority",
                str(priority),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return
    stdin = proc.stdin
    if stdin is None:
        return
    try:
        stdin.write(text)
        # Closing stdin lets systemd-cat flush the line and exit.
        stdin.close()
    except OSError:
        return


def _send_to_journal(message: str, priority: int = 6) -> None:
    """Duplicate one message into the system journal, best effort.

    The journal identifier comes from PYNTARA_JOURNAL_IDENTIFIER; an empty
    value disables journal forwarding, which unit tests use. The priority
    is the syslog level as a number, 6 (informational) by default, and is
    passed to systemd-cat as a number, never embedded in the message text.
    Informational messages flow through a reused process; a different
    priority spawns a short-lived process, because the priority is fixed
    at process start.
    """

    identifier = os.environ.get("PYNTARA_JOURNAL_IDENTIFIER", "pyntara-engine")
    if not identifier:
        return
    text = _ANSI_RE.sub("", message) + "\n"
    if priority == 6:
        _write_to_shared_journal(text, identifier)
        return
    _write_to_priority_journal(text, identifier, priority)


def log_progress(message: str, *, priority: int = 6) -> None:
    """Print one progress line of the calling task, flushed to stdout.

    The task name in the prefix comes from the calling module: one task
    module per catalog task (task-model contract), so the name can never
    diverge from the catalog. A timestamp in the project datetime format
    YYYY-MM-DD-HH-MM-SS is prepended only when more than one second has
    passed since the previous progress line, so bursts of lines stay
    compact. The journal receives the message without the timestamp at
    the given syslog priority, informational by default; tasks pass the
    configured engine progress and error priorities instead.
    """

    frame = inspect.currentframe()
    assert frame is not None
    caller = frame.f_back
    assert caller is not None
    task_name = str(caller.f_globals["__name__"]).rsplit(".", 1)[-1]
    global _last_log_time
    now = time.monotonic()
    if now - _last_log_time >= 1.0:
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        prefix = f"{timestamp} {task_name}:"
        _last_log_time = now
    else:
        prefix = f"{task_name}:"
    print(f"{prefix} {message}", flush=True)
    _send_to_journal(f"{task_name}: {message}", priority=priority)


def log_task_start(name: str, *, priority: int = 6) -> None:
    """Announce a task: empty line, colored banner, journal line.

    The console banner keeps its colors; the journal gets plain text at
    the given syslog priority, informational by default.
    """

    print()
    typer.secho(f" {name} ", bold=True, color=True)
    _send_to_journal(f"starting task: {name}", priority=priority)


def log_result_line(
    name: str,
    result: TaskResult,
    *,
    duration_seconds: float | None = None,
    to_journal: bool = True,
    priority: int = 6,
) -> None:
    """Print one task outcome line immediately after the task finishes.

    Uses the same prefixes as the final summary in the entry point, so the
    per-task report and the summary read consistently. duration_seconds is
    the wall time of the task execution and is shown in the line as
    ` in <seconds>s` with three decimal places; a task that did not run
    passes None and shows no duration. to_journal=False prints to the
    console only; the summary repeats these lines and must not duplicate
    them in the journal. The journal line carries the given syslog
    priority, informational by default.
    """

    if result.skipped:
        line = f"[skip] {name}"
        if result.message:
            line = f"{line}: {result.message}"
    elif result.success:
        line = f"[done] {name}"
        if duration_seconds is not None:
            line = f"{line} in {duration_seconds:.3f}s"
        if result.message:
            line = f"{line}: {result.message}"
    else:
        detail = result.error or "unknown error"
        line = f"[failed] {name}"
        if duration_seconds is not None:
            line = f"{line} in {duration_seconds:.3f}s"
        line = f"{line}: {detail}"
    print(line)
    if to_journal:
        _send_to_journal(line, priority=priority)
    for warning in result.warnings:
        warn_line = f"[warn] {name}: {warning}"
        print(warn_line)
        if to_journal:
            _send_to_journal(warn_line, priority=priority)


def log_event(
    message: str,
    *,
    to_stderr: bool = False,
    to_journal: bool = True,
    priority: int = 6,
) -> None:
    """Print one status line to the console and mirror it to the journal.

    to_stderr=True routes the console copy to stderr, matching typer.echo
    with err=True for error notices. to_journal=False prints to the console
    only, for lines the per-task report already journaled. The journal
    line carries the given syslog priority, informational by default.
    """

    stream = sys.stderr if to_stderr else sys.stdout
    print(message, file=stream)
    if to_journal:
        _send_to_journal(message, priority=priority)


def log_run_start(command: str, *, priority: int = 6) -> None:
    """Print the uniform command start line and mirror it to the journal.

    The line `  run : <command>` opens every command that runs through
    run_command, so walls of subprocess output in the install log are
    attributed to the command that produced them (project rules, Task
    progress output). The journal copy carries the plain text without the
    leading indent at the given syslog priority, informational by default.
    """

    print(f"  run : {command}", flush=True)
    _send_to_journal(f"run : {command}", priority=priority)


def log_run_end(
    command: str,
    exit_code: int | None,
    duration_seconds: float,
    *,
    priority: int = 6,
) -> None:
    """Print the uniform command end line and mirror it to the journal.

    The line `  /run: <exit_code> <seconds>s <command>` closes the command
    opened by log_run_start with its exit code and duration, so every
    command reports how it ended even when run_command raises. exit_code
    is None when the command was killed by its timeout, and the line then
    shows the word timeout. The duration is printed with three decimal
    places; the journal copy carries the plain text without the leading
    indent at the given syslog priority.
    """

    code_text = "timeout" if exit_code is None else str(exit_code)
    line = f"  /run: {code_text} {duration_seconds:.3f}s {command}"
    print(line, flush=True)
    _send_to_journal(line[2:], priority=priority)
