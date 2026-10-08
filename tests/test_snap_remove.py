"""Unit tests for the snap_remove task.

External commands (the dpkg status query and the apt purge) are mocked by
monkeypatching run_command in the task module and in utils, because the shared
helper package_is_installed of utils calls its own run_command; the free-space
query runs against the temporary tree, because the autouse fixture points the
system root at the directory of the test (docs/guides/developer-guide.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import FakeProc, make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import snap_remove
from pyntara.values import snap_remove as values
from pyntara.values import tasks as tasks_values

REAL_TASKS = tasks_values.CATALOG


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test its own paths so nothing of the task touches the machine."""

    monkeypatch.setattr(
        values,
        "SNAP_PATH_NAMES",
        (
            tmp_path / "snap",
            tmp_path / "var-snap",
            tmp_path / "var-cache-snapd",
            tmp_path / "var-lib-snapd",
        ),
    )


def _ctx() -> Context:
    return make_context(task_name="snap_remove", install_mode="desktop")


def _patch_run(monkeypatch: pytest.MonkeyPatch, fake_run: object) -> None:
    """Patch run_command in the task and in the shared helper that queries dpkg."""

    monkeypatch.setattr(snap_remove, "run_command", fake_run)
    monkeypatch.setattr("pyntara.utils.run_command", fake_run)


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: bool = True,
    purge_returncode: int = 0,
    calls: list[list[str]] | None = None,
) -> dict[str, bool]:
    """Replace run_command with a stand-in that answers by argv content.

    The state remembers whether the packages are installed, so a successful
    purge makes the next run observe a machine without snap, which is how the
    idempotency of the task is tested.
    """

    recorded = calls if calls is not None else []
    state = {"installed": installed}

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        recorded.append(list(command))
        joined = " ".join(command)
        if "dpkg-query" in joined:
            if state["installed"]:
                return FakeProc(0, stdout="install ok installed\n")
            return FakeProc(1, stderr="no packages found\n")
        if command[:2] == ["apt-get", "purge"]:
            if purge_returncode == 0:
                state["installed"] = False
            return FakeProc(purge_returncode, stderr="boom\n" if purge_returncode else "")
        return FakeProc(0)

    _patch_run(monkeypatch, fake_run)
    return state


def test_removes_the_snap_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _fake_run(monkeypatch, calls=calls)
    result = snap_remove.task(_ctx())
    assert result.success
    assert result.changed is True
    assert result.message is not None
    assert "snapd" in result.message
    purges = [command for command in calls if command[:2] == ["apt-get", "purge"]]
    assert purges == [["apt-get", "purge", "--yes", *values.PACKAGE_NAMES]]


def test_a_machine_without_snap_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    _fake_run(monkeypatch, installed=False, calls=calls)
    result = snap_remove.task(_ctx())
    assert result.success
    assert result.changed is False
    assert result.message == "the snap subsystem is not installed"
    assert not [command for command in calls if command[:2] == ["apt-get", "purge"]]


def test_second_run_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch)
    first = snap_remove.task(_ctx())
    second = snap_remove.task(_ctx())
    assert first.changed is True
    assert second.changed is False
    assert second.message == "the snap subsystem is not installed"


def test_a_failed_purge_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, purge_returncode=100)
    result = snap_remove.task(_ctx())
    assert result.success
    assert result.changed is False
    assert any(
        "cannot remove the snap packages" in warning for warning in result.warnings
    )


def test_a_snap_directory_that_survives_is_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run(monkeypatch)
    surviving = values.SNAP_PATH_NAMES[3]
    surviving.mkdir(parents=True)
    result = snap_remove.task(_ctx())
    assert result.success
    assert result.changed is True
    assert any(str(surviving) in warning for warning in result.warnings)


def test_the_task_belongs_to_the_installed_modes_and_not_fast_desktop() -> None:
    for mode in ("minimal", "server", "desktop"):
        assert "snap_remove" in task_catalog.default_tasks(mode, REAL_TASKS)
    assert "snap_remove" not in task_catalog.default_tasks("fast_desktop", REAL_TASKS)


def test_the_task_has_no_dependency_but_runs_after_firefox_setup() -> None:
    assert task_catalog.resolve(["snap_remove"], REAL_TASKS) == ["snap_remove"]
    order = [task.name for task in REAL_TASKS]
    assert order.index("snap_remove") > order.index("firefox_setup")
