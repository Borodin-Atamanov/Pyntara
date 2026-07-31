from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from pyntara.context import RunContext, create_run_context
from pyntara.models import AppConfig, InstallModesConfig, TaskDefinition
from pyntara.task_registry import TaskRegistry
from pyntara.task_runner import TaskRunner


class FakeSecretsStore:
    def get(self, key: str, default: str | None = None) -> str | None:
        return default


def _catalog(**overrides: dict[str, object]) -> dict[str, TaskDefinition]:
    hostname_overrides = overrides.get("hostname", {})
    users_overrides = overrides.get("users", {})
    hostname_base: dict[str, object] = {
        "name": "hostname",
        "order": 10,
        "description": "Generate hostname",
        "module": "pyntara.tasks.hostname:run",
        "depends_on": [],
        "data_subdir": "hostname",
    }
    users_base: dict[str, object] = {
        "name": "users",
        "order": 20,
        "description": "Prepare users",
        "module": "pyntara.tasks.users:run",
        "depends_on": ["hostname"],
        "data_subdir": "users",
    }
    hostname_base.update(hostname_overrides if isinstance(hostname_overrides, dict) else {})
    users_base.update(users_overrides if isinstance(users_overrides, dict) else {})
    return {
        "hostname": TaskDefinition.model_validate(hostname_base),
        "users": TaskDefinition.model_validate(users_base),
    }


def _context(
    tmp_path: Path,
    *,
    catalog: dict[str, TaskDefinition] | None = None,
    command_timeout_sec: int = 300,
) -> RunContext:
    config = AppConfig.model_validate(
        {
            "timeouts": {"command_sec": command_timeout_sec},
            "paths": {
                "task_data_dir": str(tmp_path / "task_data"),
                "secrets_dir": str(tmp_path / "secrets"),
            },
        }
    )
    return create_run_context(
        config=config,
        install_modes=InstallModesConfig(),
        task_catalog=_catalog() if catalog is None else catalog,
        secrets_store=FakeSecretsStore(),
        logger=logging.getLogger("test-runner"),
    )


def _state_payload(tmp_path: Path, task_name: str) -> dict[str, object]:
    state_file = tmp_path / "task_data" / task_name / "state.json"
    return json.loads(state_file.read_text(encoding="utf-8"))


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
    assert _state_payload(tmp_path, "hostname")["status"] == "skipped"


def test_runner_force_executes_completed_tasks(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = TaskRegistry(task_catalog=context.task_catalog)
    runner = TaskRunner(registry=registry)

    runner.run(ctx=context, task_names=["hostname", "users"], force=False)
    forced = runner.run(ctx=context, task_names=["hostname", "users"], force=True)

    assert forced.success is True
    assert [entry.status for entry in forced.executions] == ["done", "done"]


def test_runner_fingerprint_change_executes_task_again(tmp_path: Path) -> None:
    context_a = _context(tmp_path, command_timeout_sec=300)
    context_b = _context(tmp_path, command_timeout_sec=301)
    registry = TaskRegistry(task_catalog=context_a.task_catalog)
    runner = TaskRunner(registry=registry)

    runner.run(ctx=context_a, task_names=["hostname"], force=False)
    second = runner.run(ctx=context_b, task_names=["hostname"], force=False)

    assert second.success is True
    assert [entry.status for entry in second.executions] == ["done"]
    assert _state_payload(tmp_path, "hostname")["status"] == "done"


def test_runner_rejects_conflicting_task_selection(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        catalog=_catalog(hostname={"conflicts_with": ["users"]}, users={"conflicts_with": []}),
    )
    registry = TaskRegistry(task_catalog=context.task_catalog)
    runner = TaskRunner(registry=registry)

    with pytest.raises(ValueError, match="conflicts with selected task"):
        runner.run(ctx=context, task_names=["hostname", "users"], force=False)


def test_runner_fails_preflight_when_root_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pyntara.task_runner.os.geteuid", lambda: 1000)
    context = _context(tmp_path, catalog=_catalog(hostname={"requires_root": True}))
    registry = TaskRegistry(task_catalog=context.task_catalog)
    runner = TaskRunner(registry=registry)

    report = runner.run(ctx=context, task_names=["hostname"], force=False)

    assert report.success is False
    assert [entry.status for entry in report.executions] == ["failed"]
    state = _state_payload(tmp_path, "hostname")
    assert state["status"] == "failed"
    assert state["error_code"] == "CAP_ROOT_REQUIRED"


def test_runner_fails_preflight_when_network_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pyntara.task_runner._has_network_connectivity", lambda timeout_sec: False)
    context = _context(tmp_path, catalog=_catalog(hostname={"requires_network": True}))
    registry = TaskRegistry(task_catalog=context.task_catalog)
    runner = TaskRunner(registry=registry)

    report = runner.run(ctx=context, task_names=["hostname"], force=False)

    assert report.success is False
    assert [entry.status for entry in report.executions] == ["failed"]
    state = _state_payload(tmp_path, "hostname")
    assert state["status"] == "failed"
    assert state["error_code"] == "CAP_NETWORK_REQUIRED"
