from __future__ import annotations

import secrets
import string
from pathlib import Path

from ..context import RunContext
from ..models import TaskResult

_ALPHABET = string.ascii_lowercase + string.digits
_HOSTNAME_LENGTH = 9


def run(ctx: RunContext, *, force: bool = False) -> TaskResult:
    task_dir = _task_dir(ctx)
    task_dir.mkdir(parents=True, exist_ok=True)
    hostname_path = task_dir / "hostname.txt"

    if hostname_path.exists() and not force:
        return TaskResult(success=True, changed=False, message="Hostname already generated.")

    value = "".join(secrets.choice(_ALPHABET) for _ in range(_HOSTNAME_LENGTH))
    hostname_path.write_text(value, encoding="utf-8")
    return TaskResult(success=True, changed=True, message="Hostname generated.")


def _task_dir(ctx: RunContext) -> Path:
    task_def = ctx.task_catalog["hostname"]
    subdir = task_def.data_subdir or "hostname"
    return ctx.task_data_dir / subdir

