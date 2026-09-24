"""Central logging helpers for the engine.

Every own message of the engine flows through this module: task progress
lines, task banners, result lines, status events and the tracking lines of
a command. One helper renders and sends them all, so every line of a run is
shaped the same way: the moment, when more than a second passed since the
previous line that carried one, then the text of the line. A helper builds
its own text and hands it over, so the console shape and the journal copy
cannot drift apart between line kinds. The journal receives plain text
without the console moment, because the journal stamps its own time, and
without ANSI color codes. Subprocess output streams straight from
run_command and never passes through here.
"""

from __future__ import annotations

import inspect
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import TextIO

import typer

from pyntara.models import TaskResult
from pyntara.values import engine as engine_values

# ANSI color codes from typer.secho must never reach the journal.
_ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*m")

# Persistent journal process; None until the first journal message.
_journal_proc: subprocess.Popen[str] | None = None

# The journal identifier the logger writes under; None until
# configure_journal.
_journal_identifier: str | None = None


def configure_journal(identifier: str | None) -> None:
    """Tell the logger which journal identifier its messages carry.

    The logger writes before and around the load of the run values, so it
    cannot read them itself: the composition root calls this once, and so
    does the entry point of every deployed service, which passes the
    identifier of its own section. Passing None keeps the console path and
    forwards nothing to the journal, which is what a test run asks for. A
    call that replaces another also closes the process the previous one
    started, because the journal tool fixes the identifier at process
    start, so a reused process would route the next messages under the old
    name.
    """

    global _journal_identifier
    _close_shared_journal()
    _journal_identifier = identifier


def _timestamp_format() -> str:
    """The datetime format of an own line, or an empty string.

    The logger writes before the run values are known and inside components
    that are no part of the engine, so a logger nobody configured writes a
    line without a moment, which is the shape a test run wants.
    """

    return "" if _journal_identifier is None else engine_values.DATETIME_FORMAT


def _close_shared_journal() -> None:
    """Close the reused journal process, if one is running."""

    global _journal_proc
    proc = _journal_proc
    _journal_proc = None
    if proc is None:
        return
    stdin = proc.stdin
    if stdin is not None:
        try:
            stdin.close()
        except OSError:
            pass


def _rendered_command(command: tuple[str, ...], values: dict[str, str]) -> list[str]:
    """The configured journal command with its placeholders filled in.

    The helper is imported inside the function because pyntara.utils
    imports this module: a module-level import would close the cycle. The
    rendering itself belongs to pyntara.utils, the one place that fills
    placeholders in configured commands.
    """

    from pyntara.utils import substituted_command

    return substituted_command(command, values)


# Monotonic time of the previous line that carried a moment; presentation
# state only. Shared across tasks and line kinds, so a moment is printed at
# most once per second for the whole run and bursts of lines stay compact.
_last_stamp_time = 0.0


def _moment_prefix(text: str) -> str:
    """The moment that opens a line, or an empty string.

    The moment is written when more than one second has passed since the
    previous line that carried one, so a burst of lines stays compact and a
    pause is visible at the line that follows it. An empty text is a
    separator: it carries nothing and never takes the moment, so the blank
    line before a banner leaves the moment to the banner. A logger nobody
    configured writes no moment, which is the shape a test run wants.
    """

    global _last_stamp_time
    timestamp_format = _timestamp_format()
    if not timestamp_format or not text:
        return ""
    now = time.monotonic()
    if now - _last_stamp_time < 1.0:
        return ""
    _last_stamp_time = now
    return datetime.now().astimezone().strftime(timestamp_format) + " "


def _write_to_shared_journal(text: str, command: list[str]) -> None:
    """Write one line through the reused journal process, best effort.

    The shared process writes the progress entries, the level the calls of
    the run carry by default, and it is started with the priority the
    declared journal command names for them, because a journal tool fixes
    the priority at process start. A missing executable or a failed write
    never stops the run: without a journal the console and the install log
    keep working as before.
    """

    global _journal_proc
    if _journal_proc is None or _journal_proc.poll() is not None:
        executable = shutil.which(command[0])
        if executable is None:
            return
        try:
            _journal_proc = subprocess.Popen(
                [executable, *command[1:]],
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


def _write_to_priority_journal(text: str, priority: int, identifier: str) -> None:
    """Write one line through a short-lived journal process, best effort.

    A journal tool fixes the priority at process start, so a message with a
    non-default priority cannot go through the shared process: a dedicated
    process is spawned with the priority as a number and closed after the
    single line. The priority is passed as a number, never embedded in the
    message text. A missing executable or a failed write never stops the
    run.
    """

    command = _rendered_command(
        engine_values.JOURNAL_PRIORITY_COMMAND,
        {
            "identifier": identifier,
            "priority": str(priority),
        },
    )
    executable = shutil.which(command[0])
    if executable is None:
        return
    try:
        proc = subprocess.Popen(
            [executable, *command[1:]],
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


def _shared_journal_command(identifier: str) -> list[str]:
    """The command that writes the progress entries of the run.

    The progress level is a declared value, so the shared process is started
    with the declared priority command and that level.
    """

    return _rendered_command(
        engine_values.JOURNAL_PRIORITY_COMMAND,
        {
            "identifier": identifier,
            "priority": str(engine_values.PROGRESS_PRIORITY),
        },
    )


def _send_to_journal(message: str, priority: int | None = None) -> None:
    """Duplicate one message into the system journal, best effort.

    A logger nobody configured forwards nothing: the console and the install
    log keep working, and a component started outside the engine never
    guesses a name for itself. The priority is the syslog level as a number
    and is passed to the journal tool as a number, never embedded in the
    message text; a call that names none carries PROGRESS_PRIORITY, which is
    the level most messages of a run carry. Messages of that level flow
    through a reused process, a message of another level spawns a
    short-lived one, because the priority is fixed at process start.
    """

    identifier = _journal_identifier
    if identifier is None:
        return
    level = engine_values.PROGRESS_PRIORITY if priority is None else priority
    if level == engine_values.PROGRESS_PRIORITY:
        _write_to_shared_journal(
            _ANSI_RE.sub("", message) + "\n",
            _shared_journal_command(identifier),
        )
        return
    _write_to_priority_journal(_ANSI_RE.sub("", message) + "\n", level, identifier)


def _emit_line(
    text: str,
    *,
    indent: str = "",
    journal_text: str | None = None,
    to_journal: bool = True,
    priority: int | None = None,
    stream: TextIO | None = None,
    banner: bool = False,
) -> None:
    """Write one own line of the engine to the console and the journal.

    This is the one place that renders an own line, so every line of a run
    is built the same way: the moment when more than a second passed since
    the previous line that carried one, the indent of the line kind, then
    the text. The moment opens the line, so a reader finds the time of every
    line kind in the same column. The journal receives the plain text
    without the moment, because the journal stamps its own time, and without
    the indent and the ANSI codes of a banner. A banner prints in color, any
    other line prints to the given stream, the standard output by default.
    journal_text replaces the text for the journal when a line reads
    differently there, which is how a colored banner becomes the plain
    `starting task` line. to_journal=False prints to the console only.
    """

    line = f"{_moment_prefix(text)}{indent}{text}"
    if banner:
        typer.secho(line, bold=True, color=True)
    else:
        print(line, file=stream, flush=True)
    if to_journal:
        plain_text = text if journal_text is None else journal_text
        _send_to_journal(plain_text, priority=priority)


def log_progress(message: str, *, priority: int | None = None) -> None:
    """Print one progress line of the calling task, flushed to stdout.

    The task name in the prefix comes from the calling module: one task
    module per catalog task (task-model contract), so the name can never
    diverge from the catalog. The moment of the line and the mirroring into
    the journal belong to _emit_line, which every own line of the run
    shares: the journal receives the module name and the message without
    the moment, at the given syslog priority, informational by default.
    """

    frame = inspect.currentframe()
    assert frame is not None
    caller = frame.f_back
    assert caller is not None
    task_name = str(caller.f_globals["__name__"]).rsplit(".", 1)[-1]
    _emit_line(f"{task_name}: {message}", priority=priority)


def log_task_start(name: str, *, priority: int | None = None) -> None:
    """Announce a task: empty line, colored banner, journal line.

    The console banner keeps its colors; the journal gets the plain
    `starting task` text at the given syslog priority, the declared progress
    level by default. The empty separator line is printed without a journal
    copy and never takes the moment, so the moment opens the banner.
    """

    _emit_line("", to_journal=False)
    _emit_line(
        f" {name} ",
        journal_text=f"starting task: {name}",
        priority=priority,
        banner=True,
    )


def log_result_line(
    name: str,
    result: TaskResult,
    *,
    duration_seconds: float | None = None,
    to_journal: bool = True,
    priority: int | None = None,
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
    _emit_line(line, to_journal=to_journal, priority=priority)
    for warning in result.warnings:
        _emit_line(
            f"[warn] {name}: {warning}", to_journal=to_journal, priority=priority
        )


def log_event(
    message: str,
    *,
    to_stderr: bool = False,
    to_journal: bool = True,
    priority: int | None = None,
) -> None:
    """Print one status line to the console and mirror it to the journal.

    to_stderr=True routes the console copy to stderr, matching typer.echo
    with err=True for error notices. to_journal=False prints to the console
    only, for lines the per-task report already journaled. The journal
    line carries the given syslog priority, informational by default.
    """

    stream = sys.stderr if to_stderr else None
    _emit_line(message, to_journal=to_journal, priority=priority, stream=stream)


def log_run_start(command: str, *, priority: int | None = None) -> None:
    """Print the uniform command start line and mirror it to the journal.

    The line `  run : <command>` opens every command that runs through
    run_command, so walls of subprocess output in the install log are
    attributed to the command that produced them (project rules, Task
    progress output). The indent keeps the pair of tracking lines apart from
    the output they frame; the journal copy carries the plain text without
    the indent at the given syslog priority, informational by default.
    """

    _emit_line(f"run : {command}", indent="  ", priority=priority)


def log_run_end(
    command: str,
    exit_code: int | None,
    duration_seconds: float,
    *,
    priority: int | None = None,
) -> None:
    """Print the uniform command end line and mirror it to the journal.

    The line `  /run: <exit_code> <seconds>s <command>` closes the command
    opened by log_run_start with its exit code and duration, so every
    command reports how it ended even when run_command raises. exit_code
    is None when the command was killed by its timeout, and the line then
    shows the word timeout. The duration is printed with three decimal
    places; the journal copy carries the plain text without the indent at
    the given syslog priority.
    """

    code_text = "timeout" if exit_code is None else str(exit_code)
    _emit_line(
        f"/run: {code_text} {duration_seconds:.3f}s {command}",
        indent="  ",
        priority=priority,
    )
