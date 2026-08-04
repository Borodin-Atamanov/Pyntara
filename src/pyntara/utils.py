"""Shared helpers for task modules.

run_command is the single command-execution wrapper used by tasks: no
shell, real-time output streaming, timeout and return-code checking
(docs/guides/project-rules.md section 4).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping

# Provisioning commands are slow; 30 minutes is a generous ceiling that
# still prevents a hung process from blocking the whole run.
DEFAULT_TIMEOUT_SECONDS = 1800


def run_command(
    command: Iterable[str],
    *,
    extra_env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
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
