"""Unit tests for the install_gui_apps task.

The install path is the shared one of pyntara.package_set and the pin is the
shared one of pyntara.plasma_panel, so this suite exercises the wiring of this
task: which package each application installs, that an application is installed
on its own, that a launcher is pinned only when its menu entry is present, and
that every failure becomes a warning of a completed task.

All external resources (dpkg-query, apt-get) are mocked via monkeypatch and the
desktop entries are files in the temporary tree, so the tests never touch the
real system (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc, make_context

from pyntara import plasma_panel, task_catalog
from pyntara.context import Context
from pyntara.tasks import install_gui_apps
from pyntara.values import install_gui_apps as values
from pyntara.values import tasks as tasks_values

# The real catalog from the values package; the mode and order tests use it so
# they cover the actual task set.
REAL_TASKS = tasks_values.CATALOG

# Small stand-in for the shipped application list: one application with one
# package and one menu entry.
TEST_APPLICATION = values.GuiApp(
    name="Test Editor",
    packages=("test-editor",),
    desktop_file_name="test-editor.desktop",
)
TEST_LAUNCHER_ID = "applications:test-editor.desktop"


@pytest.fixture(autouse=True)
def _point_the_values_at_the_test_application(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test its own application and its own entries directory.

    The application list and the entries directory are values of the task, so
    the fixture patches them for one test and the shipped values come back
    afterwards. A test that needs another list patches the same names itself.
    """

    monkeypatch.setattr(values, "GUI_APPS", (TEST_APPLICATION,))
    monkeypatch.setattr(values, "APPLICATIONS_DIRECTORY", tmp_path / "applications")


def _ctx() -> Context:
    return make_context(task_name="install_gui_apps", install_mode="desktop")


def _write_menu_entry() -> None:
    """Create the menu entry of the test application on the disk."""

    entry = values.APPLICATIONS_DIRECTORY / TEST_APPLICATION.desktop_file_name
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("[Desktop Entry]\n", encoding="utf-8")


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: set[str],
    fail_install: bool = False,
    create_entry_on_install: bool = True,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query answers from the installed set, a successful apt-get install
    writes the menu entry as a real install would, and every other command
    succeeds. A failing install answers with a nonzero exit code, so the shared
    install path turns it into a warning of the task.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            if command[-1] in installed:
                return FakeProc(0, "install ok installed\n")
            return FakeProc(1, "")
        if command[:2] == ["apt-get", "install"]:
            if fail_install:
                raise subprocess.CalledProcessError(100, command)
            if create_entry_on_install:
                _write_menu_entry()
            return FakeProc(0)
        return FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _pin_fake(
    monkeypatch: pytest.MonkeyPatch, *, changed: bool, note: str | None = None
) -> list[str]:
    """Replace the panel pin with a recorder; return the pinned launcher ids."""

    pinned: list[str] = []

    def fake_pin(launcher_id: str, *, timeout: float) -> tuple[bool, str | None]:
        pinned.append(launcher_id)
        return changed, note

    monkeypatch.setattr(plasma_panel, "pin_launcher", fake_pin)
    return pinned


def test_install_gui_apps_is_in_the_two_desktop_modes() -> None:
    # The modes are named one by one, so a mode added later is not a failure.
    assert "install_gui_apps" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "install_gui_apps" in task_catalog.default_tasks("fast_desktop", REAL_TASKS)


def test_install_gui_apps_takes_no_dependency() -> None:
    task_def = task_catalog.by_name("install_gui_apps", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ()


def test_install_gui_apps_is_placed_after_the_browser_tasks() -> None:
    names = [task.name for task in REAL_TASKS]
    assert names.index("install_gui_apps") > names.index("firefox_setup")


def test_installs_the_application_and_pins_its_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake(monkeypatch, installed=set())
    pinned = _pin_fake(monkeypatch, changed=True)

    result = install_gui_apps.task(_ctx())

    assert result.success is True
    assert result.changed is True
    install_calls = [
        call for call in calls if call[:2] == ["apt-get", "install"]
    ]
    assert install_calls == [
        [
            "apt-get",
            "install",
            "-y",
            "-o",
            "DPkg::Lock::Timeout=600",
            "test-editor",
        ]
    ]
    assert pinned == [TEST_LAUNCHER_ID]


def test_already_installed_and_pinned_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake(monkeypatch, installed={"test-editor"})
    _write_menu_entry()
    _pin_fake(monkeypatch, changed=False)

    result = install_gui_apps.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert not any(call[:2] == ["apt-get", "install"] for call in calls)


def test_a_failed_install_is_a_warning_and_the_launcher_is_not_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake(monkeypatch, installed=set(), fail_install=True)
    pinned = _pin_fake(monkeypatch, changed=True)

    result = install_gui_apps.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert pinned == []
    assert any("test-editor" in warning for warning in result.warnings)
    assert any("menu entry" in warning for warning in result.warnings)


def test_a_missing_menu_entry_is_a_warning_and_the_launcher_is_not_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake(monkeypatch, installed={"test-editor"}, create_entry_on_install=False)
    pinned = _pin_fake(monkeypatch, changed=True)

    result = install_gui_apps.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert pinned == []
    assert any("menu entry" in warning for warning in result.warnings)


def test_a_pin_that_did_not_arrive_is_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake(monkeypatch, installed={"test-editor"})
    _write_menu_entry()
    _pin_fake(monkeypatch, changed=False, note="the running panel did not take it")

    result = install_gui_apps.task(_ctx())

    assert result.success is True
    assert "the running panel did not take it" in result.warnings


def test_a_missing_value_is_reported_and_nothing_is_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        values, "READ_VALUE_NAMES", values.READ_VALUE_NAMES + ("NOT_DECLARED",)
    )
    calls = _install_fake(monkeypatch, installed=set())
    _pin_fake(monkeypatch, changed=True)

    result = install_gui_apps.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert "not declared" in (result.message or "")
    assert calls == []
