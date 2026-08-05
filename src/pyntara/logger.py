"""Central logging helpers for the engine.

Every own message of the engine flows through this module: task progress
lines, task banners, result lines and status events. Each helper writes to
the console exactly like the code it replaces (project rules section 1.2)
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


def _send_to_journal(message: str) -> None:
    """Duplicate one message into the system journal, best effort.

    The journal identifier comes from PYNTARA_JOURNAL_IDENTIFIER; an empty
    value disables journal forwarding, which unit tests use. systemd-cat
    is started lazily and reused. A missing executable or a failed write
    never stops the run (general resilience rule): without a journal the
    console and the install log keep working as before.
    """

    identifier = os.environ.get("PYNTARA_JOURNAL_IDENTIFIER", "pyntara-engine")
    if not identifier:
        return
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
        stdin.write(_ANSI_RE.sub("", message) + "\n")
        stdin.flush()
    except OSError:
        # The journal pipe broke; stop forwarding for the rest of the run.
        _journal_proc = None


def log_progress(message: str) -> None:
    """Print one progress line of the calling task, flushed to stdout.

    The task name in the prefix comes from the calling module: one task
    module per catalog task (task-model contract), so the name can never
    diverge from the catalog. A timestamp in the project datetime format
    YYYY-MM-DD-HH-MM-SS is prepended only when more than one second has
    passed since the previous progress line, so bursts of lines stay
    compact. The journal receives the message without the timestamp.
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
    _send_to_journal(f"{task_name}: {message}")


def log_task_start(name: str) -> None:
    """Announce a task: empty line, colored banner, journal line.

    The console banner keeps its colors; the journal gets plain text.
    """

    print()
    typer.secho(f" {name} ", bold=True, color=True)
    _send_to_journal(f"starting task: {name}")


def log_result_line(
    name: str, result: TaskResult, *, to_journal: bool = True
) -> None:
    """Print one task outcome line immediately after the task finishes.

    Uses the same prefixes as the final summary in the entry point, so the
    per-task report and the summary read consistently. to_journal=False
    prints to the console only; the summary repeats these lines and must
    not duplicate them in the journal.
    """

    if result.skipped:
        detail = result.message or "not implemented"
        line = f"[skip] {name}: {detail}"
    elif result.success:
        line = f"[done] {name}"
        if result.message:
            line = f"{line}: {result.message}"
    else:
        detail = result.error or "unknown error"
        line = f"[failed] {name}: {detail}"
    print(line)
    if to_journal:
        _send_to_journal(line)


def log_event(
    message: str, *, to_stderr: bool = False, to_journal: bool = True
) -> None:
    """Print one status line to the console and mirror it to the journal.

    to_stderr=True routes the console copy to stderr, matching typer.echo
    with err=True for error notices. to_journal=False prints to the console
    only, for lines the per-task report already journaled.
    """

    stream = sys.stderr if to_stderr else sys.stdout
    print(message, file=stream)
    if to_journal:
        _send_to_journal(message)
