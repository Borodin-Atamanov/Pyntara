"""Task execution engine.

Runs tasks in resolved order, one module per task under pyntara.tasks. Each
module exposes a task(ctx) function returning TaskResult. The runner hands
each task a context whose task_name is the catalog name of that task, so a
module never writes its own name. A missing module is reported as a skipped
result so a partially implemented catalog still runs cleanly. A task that
reports a failure or raises is converted into a completed result with
warnings: a recoverable failure must never stop the run, and the entry
point counts the warnings and exits nonzero.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable
from dataclasses import replace

from pyntara.context import Context
from pyntara.logger import log_progress, log_result_line, log_task_start
from pyntara.models import TaskResult
from pyntara.utils import disk_shortage_message, free_package_download_cache
from pyntara.values import engine as engine_values


def load_task(name: str) -> Callable[[Context], TaskResult] | None:
    """Return the task callable for a name, or None when not implemented.

    A ModuleNotFoundError that names the task module itself means the module
    is not written yet, which is a normal state during incremental
    development, so the task is reported as skipped. A failure inside the
    module or inside the values it imports is raised instead, so the reason
    reaches the user as an import failure and never looks like a task nobody
    wrote.
    """

    try:
        module = importlib.import_module(f"pyntara.tasks.{name}")
    except ModuleNotFoundError as exc:
        if exc.name == f"pyntara.tasks.{name}":
            return None
        raise
    task: object = getattr(module, "task", None)
    if not callable(task):
        return None
    return task


def _warn_result(result: TaskResult) -> TaskResult:
    """Turn a failed task result into a completed result with warnings.

    A task failure is a recoverable condition by design: the run continues
    with the remaining tasks, the failed steps are reported as warnings
    and the entry point counts them and exits nonzero. Only a missing
    config, detected before any task runs, stays fatal.
    """

    if result.success or result.skipped:
        return result
    reason = result.error or "unknown error"
    return TaskResult(
        success=True,
        changed=result.changed,
        message=result.message or "completed with warnings",
        warnings=(reason,),
    )


def run_tasks(
    ctx: Context, names: list[str]
) -> tuple[list[tuple[str, TaskResult]], str | None]:
    """Run each task in order, continuing after failures.

    Each task is announced with an empty line and a green banner line, then a
    short pause before execution so the user sees which task starts (project
    rules, Task presentation). Task output streams in real time through run_command; the
    outcome line is printed right after the task finishes. Returns (name,
    result) pairs in run order. A task that is not implemented is skipped; a
    task that reports a failure or raises is converted into a completed
    result with the reason in warnings, so no task failure ever stops the
    run. The outcome line carries the task execution duration, measured
    around the task call without the start delay. The entry point prints
    the final summary, so each outcome appears twice: next to the task and
    in the summary.

    Two steps of the run live here, because this is the only place that sees
    every task. While the run economizes space, the package download cache is
    freed right after each task, so what the run downloaded never piles up.
    Before each task the free space is compared to the declared reserve, so a
    machine that is nearly full is named and the run stops there: the remaining
    tasks stay unstarted instead of filling a filesystem that nobody on the
    target machine can repair. Such a stop is returned beside the results and
    the entry point reports it as a warning of the run.
    """

    results: list[tuple[str, TaskResult]] = []
    for index, name in enumerate(names):
        shortage = disk_shortage_message()
        if shortage is not None:
            return results, (
                f"the run stopped before {name}: {shortage}; "
                f"{len(names) - index} of {len(names)} tasks were not started"
            )
        log_task_start(name)
        try:
            task = load_task(name)
        except Exception as exc:  # noqa: BLE001 - a broken import must not kill the run
            result = _warn_result(
                TaskResult(success=False, error=f"task import failed: {exc}")
            )
            log_result_line(name, result)
            results.append((name, result))
            _free_download_cache(ctx)
            continue
        if task is None:
            result = TaskResult(
                success=False,
                skipped=True,
                message=f"task module not implemented: {name}",
            )
            log_result_line(name, result)
            results.append((name, result))
            _free_download_cache(ctx)
            continue
        time.sleep(engine_values.TASK_START_DELAY_SECONDS)
        start = time.monotonic()
        try:
            result = task(replace(ctx, task_name=name))
        except Exception as exc:  # noqa: BLE001 - a raising task must not kill the run
            result = TaskResult(success=False, error=str(exc))
        duration_seconds = time.monotonic() - start
        result = _warn_result(result)
        log_result_line(name, result, duration_seconds=duration_seconds)
        results.append((name, result))
        _free_download_cache(ctx)
    return results, None


def _free_download_cache(ctx: Context) -> None:
    """Free the package download cache after a task, while the run economizes.

    The step belongs to the run rather than to a single task, because the
    cache also carries what an external installer downloaded for itself. A
    cleanup that cannot run is reported and never stops the run.
    """

    if not ctx.delete_packages_after_install:
        return
    problem = free_package_download_cache(engine_values.COMMAND_TIMEOUT_SECONDS)
    if problem is not None:
        log_progress(problem)
