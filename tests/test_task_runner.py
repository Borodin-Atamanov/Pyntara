from __future__ import annotations

import logging
from pathlib import Path

from pyntara.context import RunContext, create_run_context
from pyntara.models import AppConfig, InstallModesConfig, TaskDefinition
from pyntara.task_registry import TaskRegistry
from pyntara.task_runner import TaskRunner


class FakeSecretsStore:
    def get(self, key: str, default: str | None = None) -> str | None:
        return default


def _catalog() -> dict[str, TaskDefinition]:
    return {
        "hostname": TaskDefinition(
            name="hostname",
            order=10,
            description="Generate hostname",
            module="pyntara.tasks.hostname:run",
            depends_on=[],
            data_subdir="hostname",
        ),
        "users": TaskDefinition(
            name="users",
            order=20,
            description="Prepare users",
            module="pyntara.tasks.users:run",
            depends_on=["hostname"],
            data_subdir="users",
        ),
    }


def _context(tmp_path: Path) -> RunContext:
    config = AppConfig.model_validate(
        {
            "paths": {
                "task_data_dir": str(tmp_path / "task_data"),
                "secrets_dir": str(tmp_path / "secrets"),
            }
        }
    )
    return create_run_context(
        config=config,
        install_modes=InstallModesConfig(),
        task_catalog=_catalog(),
        secrets_store=FakeSecretsStore(),
        logger=logging.getLogger("test-runner"),
    )


def test_runner_skips_completed_idempotent_tasks(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = TaskRegistry(task_catalog=context.task_catalog)
    runner = TaskRunner(registry=registry)

    first = runner.run(ctx=context, task_names=["hostname", "users"], force=False)
    second = runner.run(ctx=context, task_names=["hostname", "users"], force=False)

    assert first.success is True
    assert [entry.status for entry in first.executions] == ["done", "done"]
    assert second.success is True
    assert [entry.status for entry in second.executions] == ["skipped", "skipped"]


def test_runner_force_executes_completed_tasks(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = TaskRegistry(task_catalog=context.task_catalog)
    runner = TaskRunner(registry=registry)

    runner.run(ctx=context, task_names=["hostname", "users"], force=False)
    forced = runner.run(ctx=context, task_names=["hostname", "users"], force=True)

    assert forced.success is True
    assert [entry.status for entry in forced.executions] == ["done", "done"]
