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
from pathlib import Path

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
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and control its outcome.

    Output streams to the terminal in real time by default; pass
    capture=True for quiet status queries. With check=True a nonzero return
    code raises CalledProcessError; with check=False the caller inspects
    returncode itself. A command that exceeds the timeout raises
    TimeoutExpired. The optional input feeds the process stdin, so a
    caller can pass data that is too large for a command argument.
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
            input=input,
        )
    return subprocess.run(
        list(command),
        env=env,
        timeout=timeout,
        check=check,
        text=True,
        input=input,
    )


def service_is_enabled(name: str, timeout: float) -> bool:
    """True when the systemd service is enabled for boot.

    systemctl is-enabled reports the boot state; "enabled" is the only
    state that means the service starts at boot, every other output
    (disabled, masked, not-found) is False.
    """

    result = run_command(
        ["systemctl", "is-enabled", name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and result.stdout.strip() == "enabled"


def service_is_active(name: str, timeout: float) -> bool:
    """True when the systemd service is currently running.

    systemctl is-active reports the runtime state; "active" is the only
    state that means the service is running, every other output (inactive,
    failed, activating) is False.
    """

    result = run_command(
        ["systemctl", "is-active", name],
        check=False,
        capture=True,
        timeout=timeout,
    )
    return result.returncode == 0 and result.stdout.strip() == "active"


def ensure_root_owner(path: Path) -> None:
    """Set owner root:root when the process runs as root.

    The installer runs under sudo, so the ownership is applied on real
    machines; non-root test runs skip the chown, because it would fail
    without privileges.
    """

    if os.geteuid() == 0:
        os.chown(path, 0, 0)


def backoff_delay(
    failures: int, base_seconds: int, multiplier: int, max_seconds: int
) -> int:
    """The pause after failures consecutive failed cycles, in seconds.

    The first failed cycle waits base_seconds, every further failure
    multiplies the pause by the integer multiplier until max_seconds; all
    values are whole seconds, so no rounding is needed. A call without
    failures returns the base, so the helper is safe at any counter
    value. The shared geometric backoff of the System Metrics retry loops
    (docs/spec/system-metrics.md, sections Schedule and retry and Report
    collector).
    """

    if failures < 1:
        return base_seconds
    return min(base_seconds * multiplier ** (failures - 1), max_seconds)
