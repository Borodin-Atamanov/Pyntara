"""Task execution engine.

Runs tasks in resolved order, one module per task under pyntara.tasks. Each
module exposes a task(ctx) function returning TaskResult. A missing or broken
module is reported as a failed result so the run continues and the summary
shows everything that did not happen.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from pyntara.context import Context
from pyntara.models import TaskResult


def load_task(name: str) -> Callable[[Context], TaskResult] | None:
    """Return the task callable for a name, or None when not implemented.

    An ImportError means the module does not exist yet, which is a normal
    state during incremental development; other import errors propagate to
    the runner and are reported as failed tasks.
    """

    try:
        module = importlib.import_module(f"pyntara.tasks.{name}")
    except ImportError:
        return None
    task: object = getattr(module, "task", None)
    if not callable(task):
        return None
    return task


def run_tasks(ctx: Context, names: list[str]) -> list[tuple[str, TaskResult]]:
    """Run each task in order, continuing after failures.

    Returns (name, result) pairs in run order. A task that is not implemented
    or raises becomes a failed result with the reason in error.
    """

    results: list[tuple[str, TaskResult]] = []
    for name in names:
        try:
            task = load_task(name)
        except Exception as exc:  # noqa: BLE001 - a broken import must not kill the run
            results.append(
                (name, TaskResult(success=False, error=f"task import failed: {exc}"))
            )
            continue
        if task is None:
            results.append(
                (name, TaskResult(success=False, error=f"task module not implemented: {name}"))
            )
            continue
        try:
            result = task(ctx)
        except Exception as exc:  # noqa: BLE001 - a raising task must not kill the run
            results.append((name, TaskResult(success=False, error=str(exc))))
            continue
        results.append((name, result))
    return results
