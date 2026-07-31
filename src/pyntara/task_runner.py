from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .context import RunContext
from .models import TaskResult
from .task_registry import TaskRegistry


@dataclass(frozen=True, slots=True)
class TaskExecution:
    task_name: str
    status: str
    result: TaskResult | None = None


@dataclass(frozen=True, slots=True)
class TaskRunReport:
    executions: list[TaskExecution]

    @property
    def success(self) -> bool:
        return not any(execution.status == "failed" for execution in self.executions)


class TaskRunner:
    def __init__(self, *, registry: TaskRegistry) -> None:
        self._registry = registry

    def run(self, *, ctx: RunContext, task_names: Iterable[str], force: bool = False) -> TaskRunReport:
        ordered_tasks = self._order_tasks(ctx=ctx, selected_task_names=list(task_names))
        executions: list[TaskExecution] = []
        for task_name in ordered_tasks:
            registered = self._registry.get(task_name)
            state_path = _task_state_file(ctx=ctx, task_name=task_name)
            if registered.definition.idempotent and not force and _is_completed(state_path):
                executions.append(TaskExecution(task_name=task_name, status="skipped"))
                continue

            result = registered.runner(ctx, force=force)
            if not result.success:
                executions.append(TaskExecution(task_name=task_name, status="failed", result=result))
                return TaskRunReport(executions=executions)

            _mark_completed(state_path)
            executions.append(TaskExecution(task_name=task_name, status="done", result=result))
        return TaskRunReport(executions=executions)

    def _order_tasks(self, *, ctx: RunContext, selected_task_names: list[str]) -> list[str]:
        requested = set(selected_task_names)
        for task_name in selected_task_names:
            if task_name not in ctx.task_catalog:
                raise KeyError(f"Task '{task_name}' is not in catalog.")

        visited: set[str] = set()
        temporary: set[str] = set()
        result: list[str] = []

        def visit(task_name: str) -> None:
            if task_name in visited:
                return
            if task_name in temporary:
                raise ValueError(f"Cyclic dependency detected for task '{task_name}'.")
            temporary.add(task_name)
            task_def = ctx.task_catalog[task_name]
            for dependency in task_def.depends_on:
                if dependency in requested:
                    visit(dependency)
            temporary.remove(task_name)
            visited.add(task_name)
            result.append(task_name)

        for task_name in sorted(
            selected_task_names, key=lambda name: (ctx.task_catalog[name].order, name)
        ):
            visit(task_name)
        return result


def _task_state_file(*, ctx: RunContext, task_name: str) -> Path:
    task_data_subdir = ctx.task_catalog[task_name].data_subdir or task_name
    task_dir = ctx.task_data_dir / task_data_subdir
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir / "state.json"


def _is_completed(state_path: Path) -> bool:
    if not state_path.exists():
        return False
    with state_path.open("r", encoding="utf-8") as state_file:
        payload = json.load(state_file)
    if not isinstance(payload, dict):
        return False
    return payload.get("completed") is True


def _mark_completed(state_path: Path) -> None:
    payload = {"completed": True}
    with state_path.open("w", encoding="utf-8") as state_file:
        json.dump(payload, state_file)

