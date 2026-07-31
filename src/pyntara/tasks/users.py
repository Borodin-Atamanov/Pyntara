from __future__ import annotations

import json
from pathlib import Path

from ..context import RunContext
from ..models import TaskResult

_DEFAULT_USERS = ("i", "j", "k")


def run(ctx: RunContext, *, force: bool = False) -> TaskResult:
    task_dir = _task_dir(ctx)
    task_dir.mkdir(parents=True, exist_ok=True)
    state_path = task_dir / "users.json"

    if state_path.exists() and not force:
        return TaskResult(success=True, changed=False, message="Users state already present.")

    payload = {
        "users": list(_DEFAULT_USERS),
        "groups": ["sudo", "users"],
    }
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    return TaskResult(success=True, changed=True, message="Users state prepared.")


def _task_dir(ctx: RunContext) -> Path:
    task_def = ctx.task_catalog["users"]
    subdir = task_def.data_subdir or "users"
    return ctx.task_data_dir / subdir

