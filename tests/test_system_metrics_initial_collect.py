"""Unit tests for the system_metrics_initial_collect task.

All external resources (subprocess, the systemd unit directory) are mocked
via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The catalog checks load the real task
catalog so the ordering guarantee is verified against the actual config.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.config import load_config
from pyntara.context import Context
from pyntara.tasks import system_metrics_initial_collect

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks
ALL_MODES = ("minimal", "server", "desktop")


def _ctx(tmp_path: Path) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        install_mode="server",
        force_tasks=frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=make_config(task_data_root=tmp_path, systemd_unit_dir=tmp_path / "systemd"),
    )


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    unit_deployed: bool,
) -> Path:
    """Point the task at a temporary systemd unit directory; return it.

    The unit file name matches the default config
    system_metrics_collector_service_unit_name, so the fixture is valid
    for the config the tests build.
    """

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    if unit_deployed:
        (systemd_dir / "system_metrics_collector.service").write_text(
            "[Unit]\nDescription=fixture\n", encoding="utf-8"
        )
    return systemd_dir


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: Callable[[list[str]], bool] | None = None,
) -> list[list[str]]:
    """Install a subprocess fake; return the recorded command calls.

    The fake patches subprocess.run where run_command reaches it, so the
    real run_command wrapper is exercised; systemctl commands succeed
    unless matched by fail.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if fail is not None and fail(command):
            raise subprocess.CalledProcessError(1, command)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_starts_collector_when_unit_deployed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit file exists: the task starts the collector non-blocking with
    # the unit name from the config.
    _install_fixtures(monkeypatch, tmp_path, unit_deployed=True)
    calls = _install_fake(monkeypatch)
    result = system_metrics_initial_collect.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert calls == [
        ["systemctl", "start", "--no-block", "system_metrics_collector.service"]
    ]


def test_start_command_comes_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another start command in the config is the argv the task runs, with
    # the configured unit name substituted.
    _install_fixtures(monkeypatch, tmp_path, unit_deployed=True)
    calls = _install_fake(monkeypatch)
    ctx = _ctx(tmp_path)
    ctx = replace(
        ctx,
        config=replace(
            ctx.config,
            system_metrics_setup=replace(
                ctx.config.system_metrics_setup,
                collector=replace(
                    ctx.config.system_metrics_setup.collector,
                    service_unit_name="collector-fixture.service",
                    start_command=(
                        "sudo",
                        "systemctl",
                        "start",
                        "--no-block",
                        "{service_unit_name}",
                    ),
                ),
            ),
        ),
    )
    unit_path = tmp_path / "systemd" / "collector-fixture.service"
    unit_path.write_text("[Unit]\nDescription=fixture\n", encoding="utf-8")
    result = system_metrics_initial_collect.task(ctx)
    assert result.success is True
    assert calls == [
        [
            "sudo",
            "systemctl",
            "start",
            "--no-block",
            "collector-fixture.service",
        ]
    ]


def test_skips_when_unit_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No unit file means the collector deployment did not happen: the task
    # skips and never touches systemctl.
    _install_fixtures(monkeypatch, tmp_path, unit_deployed=False)
    calls = _install_fake(monkeypatch)
    result = system_metrics_initial_collect.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.message == "collector unit not deployed"
    assert calls == []


def test_reports_start_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed systemctl start is an error TaskResult: the install log must
    # show it, no silent failures.
    _install_fixtures(monkeypatch, tmp_path, unit_deployed=True)
    _install_fake(
        monkeypatch,
        fail=lambda command: command[0] == "systemctl",
    )
    result = system_metrics_initial_collect.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is True
    assert result.error is not None
    assert "cannot start collector service" in result.error


def test_catalog_orders_initial_collect_after_address_tasks_and_before_final_commit_in_every_mode() -> None:
    # The collector must run after the i2pd and yggdrasil provisioning so
    # the first report carries the live anonymous addresses, and before
    # the final commit_final_system_metrics task, which is the true last
    # task of the catalog. Only relative order is a contract; the exact
    # position of the remaining tasks is free.
    for mode in ALL_MODES:
        defaults = task_catalog.default_tasks(mode, REAL_TASKS)
        initial = defaults.index("system_metrics_initial_collect")
        assert defaults.index("system_metrics_setup") < initial
        assert defaults[-1] == "commit_final_system_metrics"
        assert initial < defaults.index("commit_final_system_metrics")
        for address_task in ("i2pd_service_setup", "yggdrasil_service_setup"):
            if address_task in defaults:
                assert defaults.index(address_task) < initial


def test_catalog_depends_on_system_metrics_setup() -> None:
    # The task needs the collector deployment of system_metrics_setup and
    # belongs to every install mode, mirroring system_metrics_setup.
    task_def = next(
        task for task in REAL_TASKS if task.name == "system_metrics_initial_collect"
    )
    assert task_def.depends == ("system_metrics_setup",)
    assert task_def.modes == ALL_MODES
