from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pyntara.context import create_run_context
from pyntara.models import AppConfig, InstallModesConfig, TaskDefinition


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
        )
    }


def test_create_run_context_makes_catalog_read_only(tmp_path: Path) -> None:
    context = create_run_context(
        config=AppConfig.model_validate({"paths": {"task_data_dir": str(tmp_path / "task_data")}}),
        install_modes=InstallModesConfig(),
        task_catalog=_catalog(),
        secrets_store=FakeSecretsStore(),
        logger=logging.getLogger("test-context"),
    )

    with pytest.raises(TypeError):
        context.task_catalog["users"] = TaskDefinition(
            name="users",
            order=20,
            description="Prepare users",
            module="pyntara.tasks.users:run",
            depends_on=["hostname"],
            data_subdir="users",
        )


def test_create_run_context_creates_task_data_dir(tmp_path: Path) -> None:
    task_data_dir = tmp_path / "runtime-task-data"
    context = create_run_context(
        config=AppConfig.model_validate({"paths": {"task_data_dir": str(task_data_dir)}}),
        install_modes=InstallModesConfig(),
        task_catalog=_catalog(),
        secrets_store=FakeSecretsStore(),
        logger=logging.getLogger("test-context"),
    )

    assert context.task_data_dir == task_data_dir
    assert task_data_dir.exists() is True
