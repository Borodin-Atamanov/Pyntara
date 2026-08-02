from __future__ import annotations

import os
import select
import sys
import termios
import time
import tty
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Protocol, TextIO

from .models import TaskDefinition


class KeyReader(Protocol):
    def read_key(self, timeout_sec: float) -> str | None: ...


class _TTYKeyReader:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def read_key(self, timeout_sec: float) -> str | None:
        ready, _, _ = select.select([self._fd], [], [], timeout_sec)
        if not ready:
            return None

        key = os.read(self._fd, 1)
        if key in (b"\r", b"\n"):
            return "ENTER"
        if key == b" ":
            return "SPACE"
        if key == b"\x1b":
            sequence = self._read_escape_sequence()
            if sequence == b"[A":
                return "UP"
            if sequence == b"[B":
                return "DOWN"
            if sequence == b"[C":
                return "RIGHT"
            if sequence == b"[D":
                return "LEFT"
            return None
        return None

    def _read_escape_sequence(self) -> bytes:
        sequence = b""
        for _ in range(2):
            ready, _, _ = select.select([self._fd], [], [], 0.005)
            if not ready:
                break
            sequence += os.read(self._fd, 1)
        return sequence


def select_tasks(
    *,
    task_catalog: Mapping[str, TaskDefinition],
    mode_task_names: Iterable[str],
    pre_interaction_timeout_sec: int,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    key_reader: KeyReader | None = None,
    monotonic: Callable[[], float] | None = None,
) -> list[str]:
    ordered_tasks = _order_mode_tasks(task_catalog=task_catalog, mode_task_names=mode_task_names)
    if len(ordered_tasks) == 0:
        return []

    selected_stdin = sys.stdin if stdin is None else stdin
    selected_stdout = sys.stdout if stdout is None else stdout
    monotonic_fn = time.monotonic if monotonic is None else monotonic
    descriptions = {
        name: task_catalog[name].description
        for name in ordered_tasks
    }

    selected: set[str] = set(ordered_tasks)
    cursor_index = 0
    render_state = _RenderState(previous_lines=0)

    if not _is_interactive(stdin=selected_stdin, stdout=selected_stdout):
        return [name for name in ordered_tasks if name in selected]

    first_interaction = False
    deadline = monotonic_fn() + float(pre_interaction_timeout_sec)

    try:
        reader: KeyReader
        terminal_mode: AbstractContextManager[object]
        if key_reader is None:
            fd = selected_stdin.fileno()
            reader = _TTYKeyReader(fd)
            terminal_mode = _stdin_cbreak_mode(fd)
        else:
            reader = key_reader
            terminal_mode = nullcontext()

        with terminal_mode:
            while True:
                now = monotonic_fn()
                remaining = max(0.0, deadline - now)
                remaining_sec = int(remaining + 0.999)
                render_state = _render_task_selection(
                    stdout=selected_stdout,
                    ordered_tasks=ordered_tasks,
                    selected=selected,
                    cursor_index=cursor_index,
                    remaining_sec=remaining_sec,
                    timer_active=not first_interaction,
                    previous_lines=render_state.previous_lines,
                    descriptions=descriptions,
                )

                if not first_interaction and remaining <= 0:
                    break

                wait_timeout = 0.1
                if not first_interaction:
                    wait_timeout = min(wait_timeout, max(0.0, remaining))

                key = reader.read_key(wait_timeout)
                if key is None:
                    continue

                if key in {"UP", "DOWN", "SPACE", "LEFT", "RIGHT"}:
                    first_interaction = True

                if key in {"UP", "LEFT"}:
                    cursor_index = (cursor_index - 1) % len(ordered_tasks)
                elif key in {"DOWN", "RIGHT"}:
                    cursor_index = (cursor_index + 1) % len(ordered_tasks)
                elif key == "SPACE":
                    current = ordered_tasks[cursor_index]
                    if current in selected:
                        selected.remove(current)
                    else:
                        selected.add(current)
                        selected.update(_dependency_closure(task_catalog=task_catalog, root_task=current))
                elif key == "ENTER":
                    break
    except (OSError, ValueError, termios.error):
        return [name for name in ordered_tasks if name in selected]

    selected_stdout.write("\n")
    selected_stdout.flush()
    return [name for name in ordered_tasks if name in selected]


def select_force_mode(
    *,
    timeout_sec: int,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    key_reader: KeyReader | None = None,
    monotonic: Callable[[], float] | None = None,
) -> bool:
    selected_stdin = sys.stdin if stdin is None else stdin
    selected_stdout = sys.stdout if stdout is None else stdout
    monotonic_fn = time.monotonic if monotonic is None else monotonic

    if not _is_interactive(stdin=selected_stdin, stdout=selected_stdout):
        return False

    options = ("No", "Yes")
    selected_index = 0
    previous_len = 0
    deadline = monotonic_fn() + float(timeout_sec)

    try:
        reader: KeyReader
        terminal_mode: AbstractContextManager[object]
        if key_reader is None:
            fd = selected_stdin.fileno()
            reader = _TTYKeyReader(fd)
            terminal_mode = _stdin_cbreak_mode(fd)
        else:
            reader = key_reader
            terminal_mode = nullcontext()

        with terminal_mode:
            while True:
                remaining = max(0.0, deadline - monotonic_fn())
                remaining_sec = int(remaining + 0.999)
                previous_len = _render_force_mode_prompt(
                    stdout=selected_stdout,
                    options=options,
                    selected_index=selected_index,
                    remaining_sec=remaining_sec,
                    previous_len=previous_len,
                )

                if remaining <= 0:
                    break

                key = reader.read_key(min(0.1, remaining))
                if key in {"LEFT", "UP"}:
                    selected_index = 0
                elif key in {"RIGHT", "DOWN"}:
                    selected_index = 1
                elif key == "ENTER":
                    break
    except (OSError, ValueError, termios.error):
        return False

    selected_stdout.write("\n")
    selected_stdout.flush()
    return options[selected_index] == "Yes"


def select_force_tasks(
    *,
    selected_task_names: Iterable[str],
    task_catalog: Mapping[str, TaskDefinition],
    pre_interaction_timeout_sec: int,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    key_reader: KeyReader | None = None,
    monotonic: Callable[[], float] | None = None,
) -> set[str]:
    ordered_tasks = _order_mode_tasks(
        task_catalog=task_catalog,
        mode_task_names=list(selected_task_names),
    )
    if len(ordered_tasks) == 0:
        return set()

    selected_stdin = sys.stdin if stdin is None else stdin
    selected_stdout = sys.stdout if stdout is None else stdout
    monotonic_fn = time.monotonic if monotonic is None else monotonic

    if not _is_interactive(stdin=selected_stdin, stdout=selected_stdout):
        return set()

    selected_force: set[str] = set()
    cursor_index = 0
    first_interaction = False
    deadline = monotonic_fn() + float(pre_interaction_timeout_sec)
    render_state = _RenderState(previous_lines=0)

    try:
        reader: KeyReader
        terminal_mode: AbstractContextManager[object]
        if key_reader is None:
            fd = selected_stdin.fileno()
            reader = _TTYKeyReader(fd)
            terminal_mode = _stdin_cbreak_mode(fd)
        else:
            reader = key_reader
            terminal_mode = nullcontext()

        with terminal_mode:
            while True:
                now = monotonic_fn()
                remaining = max(0.0, deadline - now)
                remaining_sec = int(remaining + 0.999)
                render_state = _render_force_task_selection(
                    stdout=selected_stdout,
                    ordered_tasks=ordered_tasks,
                    selected=selected_force,
                    cursor_index=cursor_index,
                    remaining_sec=remaining_sec,
                    timer_active=not first_interaction,
                    previous_lines=render_state.previous_lines,
                )

                if not first_interaction and remaining <= 0:
                    break

                wait_timeout = 0.1
                if not first_interaction:
                    wait_timeout = min(wait_timeout, max(0.0, remaining))

                key = reader.read_key(wait_timeout)
                if key is None:
                    continue

                if key in {"UP", "DOWN", "SPACE", "LEFT", "RIGHT"}:
                    first_interaction = True

                if key in {"UP", "LEFT"}:
                    cursor_index = (cursor_index - 1) % len(ordered_tasks)
                elif key in {"DOWN", "RIGHT"}:
                    cursor_index = (cursor_index + 1) % len(ordered_tasks)
                elif key == "SPACE":
                    current = ordered_tasks[cursor_index]
                    if current in selected_force:
                        selected_force.remove(current)
                    else:
                        selected_force.add(current)
                elif key == "ENTER":
                    break
    except (OSError, ValueError, termios.error):
        return set()

    selected_stdout.write("\n")
    selected_stdout.flush()
    return selected_force


def _dependency_closure(
    *,
    task_catalog: Mapping[str, TaskDefinition],
    root_task: str,
) -> set[str]:
    visited: set[str] = set()

    def visit(task_name: str) -> None:
        if task_name in visited:
            return
        visited.add(task_name)
        definition = task_catalog.get(task_name)
        if definition is None:
            return
        for dependency in definition.depends_on:
            visit(dependency)

    visit(root_task)
    visited.discard(root_task)
    return visited


def _order_mode_tasks(
    *,
    task_catalog: Mapping[str, TaskDefinition],
    mode_task_names: Iterable[str],
) -> list[str]:
    names = [name for name in mode_task_names if name in task_catalog]
    return sorted(names, key=lambda name: (task_catalog[name].order, name))


def _render_force_mode_prompt(
    *,
    stdout: TextIO,
    options: tuple[str, str],
    selected_index: int,
    remaining_sec: int,
    previous_len: int,
) -> int:
    rendered_options = [
        f"[{label}]" if idx == selected_index else label for idx, label in enumerate(options)
    ]
    line = (
        f"Run selected tasks with force? (auto in {remaining_sec:02d}s): "
        + " | ".join(rendered_options)
        + " (left/right + Enter)"
    )
    padding = " " * max(0, previous_len - len(line))
    stdout.write(f"\r{line}{padding}")
    stdout.flush()
    return len(line)


class _RenderState:
    def __init__(self, *, previous_lines: int) -> None:
        self.previous_lines = previous_lines


def _render_task_selection(
    *,
    stdout: TextIO,
    ordered_tasks: list[str],
    selected: set[str],
    cursor_index: int,
    remaining_sec: int,
    timer_active: bool,
    previous_lines: int,
    descriptions: Mapping[str, str],
) -> _RenderState:
    timer_text = (
        f"auto-accept in {remaining_sec:02d}s until first action"
        if timer_active
        else "manual mode"
    )
    header = f"Select tasks ({timer_text})"
    return _render_checkbox_list(
        stdout=stdout,
        ordered_tasks=ordered_tasks,
        selected=selected,
        cursor_index=cursor_index,
        previous_lines=previous_lines,
        header=header,
        footer="Use arrows to move, Space to toggle, Enter to continue.",
        descriptions=descriptions,
    )


def _render_force_task_selection(
    *,
    stdout: TextIO,
    ordered_tasks: list[str],
    selected: set[str],
    cursor_index: int,
    remaining_sec: int,
    timer_active: bool,
    previous_lines: int,
) -> _RenderState:
    timer_text = (
        f"auto-continue in {remaining_sec:02d}s until first action"
        if timer_active
        else "manual mode"
    )
    header = f"Select force tasks ({timer_text})"
    return _render_checkbox_list(
        stdout=stdout,
        ordered_tasks=ordered_tasks,
        selected=selected,
        cursor_index=cursor_index,
        previous_lines=previous_lines,
        header=header,
        footer="Use arrows to move, Space to toggle, Enter to continue.",
        descriptions=None,
    )


def _render_checkbox_list(
    *,
    stdout: TextIO,
    ordered_tasks: list[str],
    selected: set[str],
    cursor_index: int,
    previous_lines: int,
    header: str,
    footer: str,
    descriptions: Mapping[str, str] | None,
) -> _RenderState:
    lines: list[str] = [header]
    for index, name in enumerate(ordered_tasks):
        marker = ">" if index == cursor_index else " "
        checked = "[x]" if name in selected else "[ ]"
        description = ""
        if descriptions is not None and name in descriptions and descriptions[name] != "":
            description = f" - {descriptions[name]}"
        lines.append(f"{marker} {checked} {name}{description}")
    lines.append(footer)

    if previous_lines > 0:
        stdout.write(f"\x1b[{previous_lines}F")
    stdout.write("\x1b[J")
    stdout.write("\n".join(lines))
    stdout.flush()
    return _RenderState(previous_lines=len(lines))


def _is_interactive(*, stdin: TextIO, stdout: TextIO) -> bool:
    _ = stdout
    return stdin.isatty()


@contextmanager
def _stdin_cbreak_mode(fd: int) -> Iterator[None]:
    previous = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
