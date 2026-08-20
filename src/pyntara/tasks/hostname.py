"""Task hostname: generate and persist a random proquint hostname.

The hostname is a pronounceable proquint word pair: four random bytes
encoded by the shared proquint_encode helper into two five-letter words
joined by a dash, for example lusab-babad (docs/guides/project-structure.md,
src/pyntara/utils.py). The randomness comes from the secrets module, so
the name is cryptographically strong: the hostname feeds password
generation (docs/spec/secrets-model.md) and the deterministic NextDNS
profile choice (docs/spec/networking.md), so it must not be guessable.

The task writes the name into the configured hostname file and applies it
to the running kernel through the configured set_hostname_command, so
socket.gethostname() returns the new name for the dependent tasks
(nextdns_setup_system_wide reads the hostname from the kernel). The task
is idempotent: it skips when the hostname file already carries a name that
decodes as a proquint (so it was set by this task) and the kernel already
knows it. Force mode always generates a fresh name, rewrites the file and
reapplies it. Any failure is returned as an error TaskResult: the runner
continues with the remaining tasks and never stops here.
"""

from __future__ import annotations

import secrets
import socket
import subprocess
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import proquint_decode, proquint_encode, run_command, trim_whitespace


def _generate_hostname() -> str:
    """A fresh random proquint hostname from four random bytes.

    Four bytes encode to exactly two five-letter words joined by a dash,
    the XXXXX-XXXXX shape the task persists. The secrets module provides
    cryptographically strong randomness, which the hostname needs because
    it feeds password generation and the NextDNS profile choice.
    """

    return proquint_encode(secrets.token_bytes(4))


def _read_hostname_file(path: Path) -> str | None:
    """The trimmed content of the hostname file, or None when unreadable.

    A missing file is not an error: it is the first-run state. The value
    crosses an external boundary, so it passes through the shared
    trim_whitespace helper before it is compared or reported (project
    rules, the trim rule).
    """

    try:
        return trim_whitespace(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _write_hostname_file(path: Path, name: str) -> None:
    """Write the hostname into the hostname file.

    The parent directory is created when missing, so the write works on a
    fresh system where the file does not exist yet. The file holds the
    name and a trailing newline, the conventional single-line form the
    kernel and hostnamectl expect. The write raises OSError on failure,
    which the caller reports.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{name}\n", encoding="utf-8")


def _apply_hostname(command: tuple[str, ...], name: str, timeout: float) -> str | None:
    """Apply the hostname to the running kernel; error text or None.

    The configured command updates the hostname file and the kernel name,
    so socket.gethostname() returns the new value for the dependent
    tasks. A nonzero exit or a timeout is a failure with the exception
    text; the caller decides how to report it.
    """

    try:
        run_command([*command, name], timeout=timeout)
        return None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return f"cannot apply the hostname: {exc}"


def task(ctx: Context) -> TaskResult:
    """Generate and persist a random proquint hostname; skip when done.

    The goal is reached when the hostname file carries a name that decodes
    as a proquint (so it was set by this task) and the kernel already
    knows it; the task then returns changed=False. Otherwise it generates
    a fresh name, writes the file and applies the name to the kernel.
    Force mode always generates a fresh name. Every step is reported to
    stdout as single lines that include their result. Any failure is
    returned as an error TaskResult: the runner continues with the
    remaining tasks and never stops here.
    """

    cfg = ctx.config.hostname
    timeout = ctx.config.engine.command_timeout_seconds
    force = "hostname" in ctx.force_tasks
    hostname_file = Path(cfg.hostname_file)

    current_file = _read_hostname_file(hostname_file)
    current_kernel = socket.gethostname()
    _log(
        f"checking {hostname_file}: "
        f"{current_file if current_file is not None else 'missing'}"
    )
    _log(f"checking kernel hostname: {current_kernel}")

    # A name that decodes as a proquint was set by this task; anything
    # else (missing, empty, foreign) is replaced with a fresh name. An
    # empty name is never ours, even though proquint_decode of an empty
    # string returns empty bytes rather than None.
    file_is_ours = (
        current_file is not None
        and current_file != ""
        and proquint_decode(current_file) is not None
    )
    if force or not file_is_ours:
        name = _generate_hostname()
        _log(f"generated hostname: {name}")
    else:
        name = current_file or _generate_hostname()
        _log(f"using existing hostname: {name}")

    needs_write = force or not file_is_ours
    needs_apply = force or current_kernel != name

    if not needs_write and not needs_apply:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    if needs_write:
        try:
            _write_hostname_file(hostname_file, name)
        except OSError as exc:
            return TaskResult(
                success=False, error=f"cannot write {hostname_file}: {exc}"
            )
        _log(f"wrote {hostname_file}: {name}")

    if needs_apply:
        error = _apply_hostname(cfg.set_hostname_command, name, timeout)
        if error is not None:
            return TaskResult(success=False, error=error)
        _log(f"applied kernel hostname: {name}")

    return TaskResult(success=True, changed=True, message=f"hostname set to {name}")
