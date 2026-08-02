from __future__ import annotations

import os
import pty
import select
import subprocess
import time
from pathlib import Path


def test_testtty_pipe_to_bash_allows_interactive_toggle() -> None:
    started_at = time.monotonic()
    script_path = Path(__file__).parent / "fixtures" / "bootstrap" / "testtty.sh"
    script_bytes = script_path.read_bytes()

    master_fd, slave_fd = pty.openpty()
    slave_tty_path = os.ttyname(slave_fd)
    read_fd, write_fd = os.pipe()
    env = dict(os.environ)
    env["PYNTARA_CLI_STDIN_PATH"] = slave_tty_path

    proc = subprocess.Popen(
        ["bash", "-s"],
        stdin=read_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        close_fds=True,
    )

    os.close(read_fd)
    os.close(slave_fd)

    try:
        written = 0
        while written < len(script_bytes):
            written += os.write(write_fd, script_bytes[written:])
    finally:
        os.close(write_fd)

    output = b""
    deadline = time.monotonic() + 3.2

    def _read_chunk(fd: int) -> bytes:
        try:
            return os.read(fd, 4096)
        except OSError:
            return b""

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break

        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if ready:
            chunk = _read_chunk(master_fd)
            if chunk:
                output += chunk
            else:
                break

        # Wait for the rendered checkbox line to ensure Python switched to cbreak mode.
        if b"demo-task" in output:
            os.write(master_fd, b" ")
            time.sleep(0.06)
            os.write(master_fd, b"\r")
            break

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if not ready:
            continue
        chunk = _read_chunk(master_fd)
        if chunk:
            output += chunk
        else:
            break
        if b"TESTTTY_DONE" in output:
            break

    try:
        os.write(master_fd, b"exit\r")
    except OSError:
        pass

    try:
        proc.wait(timeout=0.6)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=0.6)
    finally:
        os.close(master_fd)

    text = output.decode("utf-8", errors="replace")
    assert "TESTTTY_UI_READY" in text, text
    assert "TESTTTY_DONE selected=1" in text, text
    assert proc.returncode == 0, text
    assert time.monotonic() - started_at < 4.0, text
