from __future__ import annotations

from typing import Protocol

from .context import RunContext
from .models import TaskResult


class TaskCallable(Protocol):
    def __call__(self, ctx: RunContext, *, force: bool = False) -> TaskResult:
        ...

