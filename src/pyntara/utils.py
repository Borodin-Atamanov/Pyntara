"""Shared helpers for task modules.

run_command is the single command-execution wrapper used by tasks: no
shell, real-time output streaming, timeout and return-code checking
(docs/guides/project-rules.md section 4). The timeout is a required
parameter: the value comes from config.toml through Context, never from a
hardcoded default (architecture contract section 3).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping

# apt must never ask questions; every package operation runs noninteractive.
# The single definition lives here so tasks cannot diverge.
APT_NONINTERACTIVE_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


def run_command(
    command: Iterable[str],
    *,
    timeout: float,
    extra_env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and control its outcome.

    Output streams to the terminal in real time by default; pass
    capture=True for quiet status queries. With check=True a nonzero return
    code raises CalledProcessError; with check=False the caller inspects
    returncode itself. A command that exceeds the timeout raises
    TimeoutExpired.
    """

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    if capture:
        return subprocess.run(
            list(command),
            env=env,
            timeout=timeout,
            check=check,
            capture_output=True,
            text=True,
        )
    return subprocess.run(
        list(command),
        env=env,
        timeout=timeout,
        check=check,
        text=True,
    )
