from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import subprocess
from typing import Sequence


def run_command(
    *,
    args: Sequence[str],
    timeout_sec: int,
    cwd: Path | None = None,
    log_file: Path | None = None,
    stream_to_console: bool = True,
) -> subprocess.CompletedProcess[str]:
    effective_cwd = cwd if cwd is not None else Path.cwd()
    effective_log = log_file if log_file is not None else effective_cwd / "logs" / "commands.log"
    effective_log.parent.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    command_text = " ".join(args)

    with effective_log.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"[{started_at}] $ {command_text}\n")
        process = subprocess.Popen(
            list(args),
            cwd=str(effective_cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        )
        try:
            stdout_lines: list[str] = []
            assert process.stdout is not None
            for line in process.stdout:
                stdout_lines.append(line)
                log_handle.write(line)
                log_handle.flush()
                if stream_to_console:
                    print(line, end="")
            return_code = process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired as timeout_error:
            process.kill()
            process.wait()
            raise RuntimeError(
                f"Command timed out after {timeout_sec}s: {command_text}"
            ) from timeout_error

    completed = subprocess.CompletedProcess(
        args=list(args),
        returncode=return_code,
        stdout="".join(stdout_lines),
        stderr=None,
    )

    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed with code {completed.returncode}: {command_text}. "
            f"See log: {effective_log}"
        )
    return completed
