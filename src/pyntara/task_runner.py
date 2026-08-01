from __future__ import annotations

import hashlib
import json
import os
import socket
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .context import RunContext
from .models import TaskDefinition, TaskResult, TaskStateStatus
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


@dataclass(frozen=True, slots=True)
class TaskState:
    status: TaskStateStatus
    state_version: int
    attempt: int
    input_fingerprint: str | None = None
    last_run_started_at: str | None = None
    last_run_finished_at: str | None = None
    changed: bool | None = None
    error_code: str | None = None
    error_message: str | None = None
    result_message: str | None = None


class TaskRunner:
    def __init__(self, *, registry: TaskRegistry) -> None:
        self._registry = registry

    def run(
        self,
        *,
        ctx: RunContext,
        task_names: Iterable[str],
        force: bool = False,
        force_task_names: Iterable[str] | None = None,
    ) -> TaskRunReport:
        ordered_tasks = self._order_tasks(ctx=ctx, selected_task_names=list(task_names))
        self._validate_selection_conflicts(
            ordered_tasks=ordered_tasks, task_catalog=ctx.task_catalog
        )
        executions: list[TaskExecution] = []
        force_task_name_set = set(force_task_names or [])

        for task_name in ordered_tasks:
            registered = self._registry.get(task_name)
            task_force = force or task_name in force_task_name_set
            state_path = _task_state_file(ctx=ctx, task_name=task_name)
            previous_state = _load_task_state(
                state_path=state_path, definition=registered.definition
            )
            current_fingerprint = _compute_input_fingerprint(
                ctx=ctx, task_name=task_name, definition=registered.definition
            )
            now = _utc_now()

            if (
                registered.definition.idempotent
                and not task_force
                and previous_state.status == "done"
                and previous_state.input_fingerprint == current_fingerprint
            ):
                skipped_state = TaskState(
                    status="skipped",
                    state_version=registered.definition.state_version,
                    attempt=previous_state.attempt,
                    input_fingerprint=current_fingerprint,
                    last_run_started_at=now,
                    last_run_finished_at=now,
                    changed=False,
                    result_message="Task skipped due to matching input fingerprint.",
                )
                _save_task_state(state_path=state_path, state=skipped_state)
                executions.append(TaskExecution(task_name=task_name, status="skipped"))
                continue

            preflight_error = _preflight_check(ctx=ctx, definition=registered.definition)
            if preflight_error is not None:
                failed_state = TaskState(
                    status="failed",
                    state_version=registered.definition.state_version,
                    attempt=previous_state.attempt + 1,
                    input_fingerprint=current_fingerprint,
                    last_run_started_at=now,
                    last_run_finished_at=now,
                    changed=False,
                    error_code=preflight_error[0],
                    error_message=preflight_error[1],
                )
                _save_task_state(state_path=state_path, state=failed_state)
                executions.append(
                    TaskExecution(
                        task_name=task_name,
                        status="failed",
                        result=TaskResult(success=False, changed=False, error=preflight_error[1]),
                    )
                )
                return TaskRunReport(executions=executions)

            running_state = TaskState(
                status="running",
                state_version=registered.definition.state_version,
                attempt=previous_state.attempt + 1,
                input_fingerprint=current_fingerprint,
                last_run_started_at=now,
            )
            _save_task_state(state_path=state_path, state=running_state)

            try:
                result = registered.runner(ctx, force=task_force)
            except Exception as task_error:
                failed_message = f"Task raised exception: {task_error}"
                failed_state = TaskState(
                    status="failed",
                    state_version=registered.definition.state_version,
                    attempt=running_state.attempt,
                    input_fingerprint=current_fingerprint,
                    last_run_started_at=running_state.last_run_started_at,
                    last_run_finished_at=_utc_now(),
                    changed=False,
                    error_code="TASK_RUNTIME_EXCEPTION",
                    error_message=failed_message,
                )
                _save_task_state(state_path=state_path, state=failed_state)
                executions.append(
                    TaskExecution(
                        task_name=task_name,
                        status="failed",
                        result=TaskResult(success=False, changed=False, error=failed_message),
                    )
                )
                return TaskRunReport(executions=executions)

            if not result.success:
                failed_state = TaskState(
                    status="failed",
                    state_version=registered.definition.state_version,
                    attempt=running_state.attempt,
                    input_fingerprint=current_fingerprint,
                    last_run_started_at=running_state.last_run_started_at,
                    last_run_finished_at=_utc_now(),
                    changed=result.changed,
                    error_code="TASK_REPORTED_FAILURE",
                    error_message=result.error or "Task reported failure without error message.",
                    result_message=result.message,
                )
                _save_task_state(state_path=state_path, state=failed_state)
                executions.append(
                    TaskExecution(task_name=task_name, status="failed", result=result)
                )
                return TaskRunReport(executions=executions)

            done_state = TaskState(
                status="done",
                state_version=registered.definition.state_version,
                attempt=running_state.attempt,
                input_fingerprint=current_fingerprint,
                last_run_started_at=running_state.last_run_started_at,
                last_run_finished_at=_utc_now(),
                changed=result.changed,
                result_message=result.message,
            )
            _save_task_state(state_path=state_path, state=done_state)
            executions.append(TaskExecution(task_name=task_name, status="done", result=result))
        return TaskRunReport(executions=executions)

    def _validate_selection_conflicts(
        self, *, ordered_tasks: list[str], task_catalog: Mapping[str, TaskDefinition]
    ) -> None:
        selected = set(ordered_tasks)
        for task_name in ordered_tasks:
            for conflict in task_catalog[task_name].conflicts_with:
                if conflict in selected:
                    raise ValueError(
                        f"Task '{task_name}' conflicts with selected task '{conflict}'."
                    )

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


def _preflight_check(*, ctx: RunContext, definition: TaskDefinition) -> tuple[str, str] | None:
    if definition.requires_root and os.geteuid() != 0:
        return ("CAP_ROOT_REQUIRED", "Task requires root privileges.")
    if definition.requires_network and not _has_network_connectivity(timeout_sec=2.0):
        return ("CAP_NETWORK_REQUIRED", "Task requires active network connectivity.")
    if definition.requires_secrets:
        try:
            ctx.secrets_store.get("__pyntara_healthcheck__", None)
        except RuntimeError:
            return ("CAP_SECRETS_REQUIRED", "Task requires loaded secrets store.")
    return None


def _compute_input_fingerprint(
    *, ctx: RunContext, task_name: str, definition: TaskDefinition
) -> str:
    payload = {
        "task_name": task_name,
        "definition": definition.model_dump(mode="json"),
        "config": ctx.config.model_dump(mode="json"),
        "mode_defaults": ctx.install_modes.model_dump(mode="json"),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _has_network_connectivity(*, timeout_sec: float) -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=timeout_sec):
            return True
    except OSError:
        return False


def _task_state_file(*, ctx: RunContext, task_name: str) -> Path:
    task_data_subdir = ctx.task_catalog[task_name].data_subdir or task_name
    task_dir = ctx.task_data_dir / task_data_subdir
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir / "state.json"


def _load_task_state(*, state_path: Path, definition: TaskDefinition) -> TaskState:
    if not state_path.exists():
        return TaskState(status="pending", state_version=definition.state_version, attempt=0)
    with state_path.open("r", encoding="utf-8") as state_file:
        payload = json.load(state_file)
    if not isinstance(payload, dict):
        return TaskState(status="pending", state_version=definition.state_version, attempt=0)

    status = _parse_status(payload.get("status"))

    attempt = payload.get("attempt")
    parsed_attempt = attempt if isinstance(attempt, int) and attempt >= 0 else 0

    return TaskState(
        status=status,
        state_version=payload.get("state_version", definition.state_version),
        attempt=parsed_attempt,
        input_fingerprint=_as_optional_str(payload.get("input_fingerprint")),
        last_run_started_at=_as_optional_str(payload.get("last_run_started_at")),
        last_run_finished_at=_as_optional_str(payload.get("last_run_finished_at")),
        changed=payload.get("changed") if isinstance(payload.get("changed"), bool) else None,
        error_code=_as_optional_str(payload.get("error_code")),
        error_message=_as_optional_str(payload.get("error_message")),
        result_message=_as_optional_str(payload.get("result_message")),
    )


def _save_task_state(*, state_path: Path, state: TaskState) -> None:
    with state_path.open("w", encoding="utf-8") as state_file:
        json.dump(asdict(state), state_file, indent=2, sort_keys=True)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _as_optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _parse_status(value: Any) -> TaskStateStatus:
    if value == "pending":
        return "pending"
    if value == "running":
        return "running"
    if value == "done":
        return "done"
    if value == "failed":
        return "failed"
    if value == "skipped":
        return "skipped"
    return "pending"
