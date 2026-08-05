"""Task execution engine.

Runs tasks in resolved order, one module per task under pyntara.tasks. Each
module exposes a task(ctx) function returning TaskResult. A missing module is
reported as a skipped result so a partially implemented catalog still runs
cleanly; a broken module is a failed result. Neither stops the run, and the
summary shows everything that was skipped or failed.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable

from pyntara.context import Context
from pyntara.logger import log_result_line, log_task_start
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

    Each task is announced with an empty line and a green banner line, then a
    short pause before execution so the user sees which task starts (project
    rules section 1.1). Task output streams in real time through run_command; the
    outcome line is printed right after the task finishes. Returns (name,
    result) pairs in run order. A task that is not implemented or raises
    becomes a failed result with the reason in error. The entry point prints
    the final summary, so each outcome appears twice: next to the task and
    in the summary.
    """

    results: list[tuple[str, TaskResult]] = []
    for name in names:
        log_task_start(name)
        try:
            task = load_task(name)
        except Exception as exc:  # noqa: BLE001 - a broken import must not kill the run
            result = TaskResult(success=False, error=f"task import failed: {exc}")
            log_result_line(name, result)
            results.append((name, result))
            continue
        if task is None:
            result = TaskResult(
                success=False,
                skipped=True,
                message=f"task module not implemented: {name}",
            )
            log_result_line(name, result)
            results.append((name, result))
            continue
        time.sleep(ctx.config.engine.task_start_delay_seconds)
        try:
            result = task(ctx)
        except Exception as exc:  # noqa: BLE001 - a raising task must not kill the run
            result = TaskResult(success=False, error=str(exc))
        log_result_line(name, result)
        results.append((name, result))
    return results
