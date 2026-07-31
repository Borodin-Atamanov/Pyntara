from __future__ import annotations

import types
from collections.abc import Iterator
from typing import Any

import pytest

from pyntara.models import TaskDefinition
from pyntara.task_registry import TaskRegistry


@pytest.fixture()
def install_fake_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    module = types.ModuleType("fake_tasks")

    def _fake_import(name: str, package: str | None = None) -> Any:
        if name == "fake_tasks":
            return module
        import importlib

        return importlib.import_module(name, package)

    monkeypatch.setattr("pyntara.task_registry.import_module", _fake_import)
    yield module


def test_registry_rejects_task_without_ctx_parameter(
    install_fake_module: types.ModuleType,
) -> None:
    def wrong_signature(force: bool = False) -> object:
        return object()

    install_fake_module.run = wrong_signature
    registry = TaskRegistry(
        task_catalog={
            "broken": TaskDefinition(
                name="broken",
                order=10,
                description="Broken task",
                module="fake_tasks:run",
                depends_on=[],
            )
        }
    )

    with pytest.raises(ValueError, match="must accept 'ctx'"):
        registry.get("broken")
