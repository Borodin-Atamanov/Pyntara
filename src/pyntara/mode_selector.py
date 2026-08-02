from __future__ import annotations

import os
import select
import sys
import termios
import time
import tty
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Protocol, TextIO

from .models import InstallModesConfig

_MODE_OPTIONS: tuple[str, str, str] = ("minimal", "server", "desktop")


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


def select_install_mode(
    *,
    install_modes: InstallModesConfig,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    key_reader: KeyReader | None = None,
    monotonic: Callable[[], float] | None = None,
) -> str:
    selected_env = dict(os.environ) if env is None else env
    selected_stdin = sys.stdin if stdin is None else stdin
    selected_stdout = sys.stdout if stdout is None else stdout
    monotonic_fn = time.monotonic if monotonic is None else monotonic

    default_mode = _detect_default_mode(install_modes=install_modes, env=selected_env)
    if default_mode not in _MODE_OPTIONS:
        raise ValueError(f"Unsupported default mode '{default_mode}'.")

    if not _is_interactive(stdin=selected_stdin, stdout=selected_stdout):
        return default_mode

    selected_index = _MODE_OPTIONS.index(default_mode)
    timeout_sec = float(install_modes.auto_select_timeout_sec)
    deadline = monotonic_fn() + timeout_sec
    rendered_len = 0

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
                rendered_len = _render_prompt(
                    stdout=selected_stdout,
                    selected_index=selected_index,
                    remaining_sec=remaining_sec,
                    previous_len=rendered_len,
                )
                if remaining <= 0:
                    break

                key = reader.read_key(min(0.1, remaining))
                if key in {"UP", "LEFT"}:
                    selected_index = (selected_index - 1) % len(_MODE_OPTIONS)
                elif key in {"DOWN", "RIGHT"}:
                    selected_index = (selected_index + 1) % len(_MODE_OPTIONS)
                elif key == "ENTER":
                    break
    except (OSError, ValueError, termios.error):
        return default_mode

    selected_stdout.write("\n")
    selected_stdout.flush()
    return _MODE_OPTIONS[selected_index]


def _detect_default_mode(*, install_modes: InstallModesConfig, env: Mapping[str, str]) -> str:
    is_desktop = "DISPLAY" in env or "WAYLAND_DISPLAY" in env
    if is_desktop:
        return str(install_modes.default_desktop_mode)
    return str(install_modes.default_server_mode)


def _is_interactive(*, stdin: TextIO, stdout: TextIO) -> bool:
    _ = stdout
    return stdin.isatty()


def _render_prompt(
    *,
    stdout: TextIO,
    selected_index: int,
    remaining_sec: int,
    previous_len: int,
) -> int:
    options = [
        f"[{mode}]" if index == selected_index else mode
        for index, mode in enumerate(_MODE_OPTIONS)
    ]
    line = (
        f"Select install mode (auto in {remaining_sec:02d}s): "
        + " | ".join(options)
        + " (arrows + Enter)"
    )
    padding = " " * max(0, previous_len - len(line))
    stdout.write(f"\r{line}{padding}")
    stdout.flush()
    return len(line)


@contextmanager
def _stdin_cbreak_mode(fd: int) -> Iterator[None]:
    previous = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
