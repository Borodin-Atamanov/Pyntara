"""Unit tests for the tmp_to_disk task.

Every external resource is replaced by a recorded fake: augtool, findmnt,
systemctl, rsync and the package installer. The fake keeps a small world whose
state the task changes, so a successful step is visible to the next read the
task makes, exactly as on a machine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc, make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import tmp_to_disk
from pyntara.values import tasks as tasks_values
from pyntara.values import tmp_to_disk as values

REAL_TASKS = tasks_values.CATALOG


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give the task its own paths so no test touches the machine."""

    mount_point = tmp_path / "tmp"
    mount_point.mkdir()
    mount_point.chmod(values.TMP_DIRECTORY_MODE)
    monkeypatch.setattr(values, "TMP_MOUNT_POINT", mount_point)
    monkeypatch.setattr(values, "FSTAB_PATH", tmp_path / "fstab")
    monkeypatch.setattr(values, "STAGING_DIRECTORY_PATH", tmp_path / "var-tmp")


def _ctx() -> Context:
    return make_context(task_name="tmp_to_disk", install_mode="desktop")


class _World:
    """The machine the fake commands answer about."""

    def __init__(self, *, mounted: bool, entry_present: bool, unit_state: str) -> None:
        self.mounted = mounted
        self.entry_present = entry_present
        self.unit_state = unit_state
        self.release_on_stop = True
        self.lazy_unmount_works = True
        self.rsync_fails = False
        self.events: list[str] = []
        self.scripts: list[str] = []

    def fstab_print(self) -> str:
        base = f"/files{values.FSTAB_PATH}/"
        if not self.entry_present:
            return ""
        return (
            f'{base}4 = (none)\n'
            f'{base}4/spec = "tmpfs"\n'
            f'{base}4/file = "{values.TMP_MOUNT_POINT}"\n'
            f'{base}4/vfstype = "{values.TMPFS_TYPE}"\n'
        )


def _install_world(monkeypatch: pytest.MonkeyPatch, world: _World) -> None:
    """Replace every command of the task with an answer from the world."""

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        name = command[0]
        if name == "augtool":
            script = str(kwargs.get("input", ""))
            world.scripts.append(script)
            if "print " in script:
                return FakeProc(0, world.fstab_print())
            if "rm " in script:
                world.events.append("remove-fstab-entry")
                world.entry_present = False
                return FakeProc(0, "Saved 1 file(s)\n")
            return FakeProc(0, "")
        if name == "findmnt":
            if world.mounted:
                return FakeProc(0, f"{values.TMPFS_TYPE}\n")
            return FakeProc(1, "")
        if name == "systemctl":
            if "is-enabled" in command:
                return FakeProc(0, f"{world.unit_state}\n")
            if "mask" in command:
                world.events.append("mask-unit")
                world.unit_state = values.UNIT_MASKED_STATE
                return FakeProc(0, "")
            if "daemon-reload" in command:
                world.events.append("daemon-reload")
                return FakeProc(0, "")
            if "stop" in command:
                world.events.append("stop-unit")
                if world.release_on_stop:
                    world.mounted = False
                    return FakeProc(0, "")
                raise subprocess.CalledProcessError(1, command)
            return FakeProc(0, "")
        if name == "umount":
            world.events.append("lazy-umount")
            if world.lazy_unmount_works:
                world.mounted = False
                return FakeProc(0, "")
            raise subprocess.CalledProcessError(32, command)
        if name == "rsync":
            world.events.append("rsync")
            if world.rsync_fails:
                raise subprocess.CalledProcessError(23, command)
            return FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    def fake_install(ctx: object, packages: object) -> tuple[list[str], list[str], list[tuple[str, str]], list[str]]:
        return [], [], [], []

    monkeypatch.setattr(tmp_to_disk, "run_command", fake_run)
    monkeypatch.setattr(tmp_to_disk, "install_missing_packages", fake_install)


def test_the_target_state_needs_no_change(monkeypatch: pytest.MonkeyPatch) -> None:
    # The mount is on the disk, the fstab carries no memory entry and the unit
    # is masked: the task reports the target state and runs nothing that
    # changes the machine.
    world = _World(mounted=False, entry_present=False, unit_state="masked")
    _install_world(monkeypatch, world)

    result = tmp_to_disk.task(_ctx())

    assert result.changed is False
    assert result.message == "already configured"
    assert result.warnings == ()
    assert world.events == []


def test_the_memory_directory_moves_to_the_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A fresh machine: a memory mount, an fstab entry and an enabled unit. The
    # contents are copied first, because releasing the mount destroys them, and
    # the saved copy reaches the disk after the mount is gone.
    world = _World(mounted=True, entry_present=True, unit_state="enabled")
    _install_world(monkeypatch, world)

    result = tmp_to_disk.task(_ctx())

    assert result.changed is True
    assert result.message == f"{values.TMP_MOUNT_POINT} is on the disk"
    assert result.warnings == ()
    assert world.events == [
        "rsync",
        "remove-fstab-entry",
        "mask-unit",
        "daemon-reload",
        "stop-unit",
        "rsync",
    ]
    assert not any((tmp_path / "var-tmp").iterdir())


def test_the_augtool_script_names_the_lens_and_the_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fstab is edited through augeas with its own lens, and the entry is
    # selected by the mount point and the filesystem type, so no other line can
    # be touched and no syntax of the file is written by the task.
    world = _World(mounted=False, entry_present=True, unit_state="masked")
    _install_world(monkeypatch, world)

    tmp_to_disk.task(_ctx())

    removal = [script for script in world.scripts if "rm " in script]
    assert len(removal) == 1
    assert values.FSTAB_LENS in removal[0]
    assert f"file='{values.TMP_MOUNT_POINT}'" in removal[0]
    assert f"vfstype='{values.TMPFS_TYPE}'" in removal[0]
    assert "save" in removal[0]


def test_a_memory_mount_without_an_fstab_entry_is_still_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The stock image mounts the directory from the vendor unit alone, so the
    # task must release the mount and mask the unit without editing the fstab.
    world = _World(mounted=True, entry_present=False, unit_state="enabled")
    _install_world(monkeypatch, world)

    result = tmp_to_disk.task(_ctx())

    assert result.changed is True
    assert "remove-fstab-entry" not in world.events
    assert "stop-unit" in world.events
    assert "mask-unit" in world.events


def test_an_already_masked_unit_is_not_masked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A machine whose unit was masked but whose session still runs the memory
    # mount needs only the release, not a second mask.
    world = _World(mounted=True, entry_present=False, unit_state="masked")
    _install_world(monkeypatch, world)

    result = tmp_to_disk.task(_ctx())

    assert result.changed is True
    assert "mask-unit" not in world.events
    assert "stop-unit" in world.events


def test_a_failed_copy_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # The contents of a memory mount are lost the moment it is released, so a
    # copy that could not be made stops the task before any configuration is
    # touched: the machine keeps the working memory mount.
    world = _World(mounted=True, entry_present=True, unit_state="enabled")
    world.rsync_fails = True
    _install_world(monkeypatch, world)

    result = tmp_to_disk.task(_ctx())

    assert result.changed is False
    assert "could not be copied" in (result.message or "")
    assert any("cannot copy" in warning for warning in result.warnings)
    assert "remove-fstab-entry" not in world.events
    assert "mask-unit" not in world.events
    assert "stop-unit" not in world.events


def test_a_mount_that_stays_in_memory_keeps_the_saved_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A process may still hold the memory mount. The boot configuration is done
    # and the saved copy stays on the disk, and the warning names its path so
    # the data is never lost without a word.
    world = _World(mounted=True, entry_present=True, unit_state="enabled")
    world.release_on_stop = False
    world.lazy_unmount_works = False
    _install_world(monkeypatch, world)

    result = tmp_to_disk.task(_ctx())

    assert result.changed is True
    assert (result.message or "").startswith(
        f"{values.TMP_MOUNT_POINT} stays in memory until the next boot"
    )
    assert any("are kept in" in warning for warning in result.warnings)
    staged = list((tmp_path / "var-tmp").iterdir())
    assert len(staged) == 1
    assert staged[0].name.startswith(values.STAGING_DIRECTORY_PREFIX)
    assert "remove-fstab-entry" in world.events
    assert "mask-unit" in world.events
    assert "lazy-umount" in world.events


def test_a_busy_mount_is_detached_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A long-lived process holds a working directory in the memory mount, so the
    # unit stop fails. The lazy unmount detaches the mount at once, the path
    # shows the disk directory, and the saved contents reach it.
    world = _World(mounted=True, entry_present=True, unit_state="enabled")
    world.release_on_stop = False
    _install_world(monkeypatch, world)

    result = tmp_to_disk.task(_ctx())

    assert result.changed is True
    assert result.message == f"{values.TMP_MOUNT_POINT} is on the disk"
    assert result.warnings == ()
    assert "lazy-umount" in world.events
    assert world.events[-1] == "rsync"
    assert not any((tmp_path / "var-tmp").iterdir())


def test_the_disk_directory_gets_the_temporary_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The directory that appears under a released memory mount keeps the mode
    # every program expects of the temporary directory.
    mount_point = values.TMP_MOUNT_POINT
    mount_point.chmod(0o700)
    world = _World(mounted=False, entry_present=False, unit_state="masked")
    _install_world(monkeypatch, world)

    result = tmp_to_disk.task(_ctx())

    assert result.changed is True
    assert mount_point.stat().st_mode & values.DIRECTORY_PERMISSION_MASK == (
        values.TMP_DIRECTORY_MODE
    )


def test_a_missing_value_is_a_warning_and_no_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A value that is not declared costs the task and never the run.
    world = _World(mounted=True, entry_present=True, unit_state="enabled")
    _install_world(monkeypatch, world)
    monkeypatch.delattr(values, "SERVICE_UNIT_NAME")

    result = tmp_to_disk.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert any("SERVICE_UNIT_NAME" in warning for warning in result.warnings)
    assert world.events == []


def test_the_task_is_in_the_catalog_without_dependencies() -> None:
    # The mode membership and the empty dependency list are the interface of
    # the task, so they are checked against the real catalog.
    records = {record.name: record for record in REAL_TASKS}
    record = records["tmp_to_disk"]
    assert record.depends == ()
    assert record.modes == ("desktop", "fast_desktop")


def test_the_task_runs_in_the_desktop_modes_only() -> None:
    # The memory temporary directory is a desktop matter; a server keeps it.
    assert "tmp_to_disk" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "tmp_to_disk" in task_catalog.default_tasks("fast_desktop", REAL_TASKS)
    assert "tmp_to_disk" not in task_catalog.default_tasks("minimal", REAL_TASKS)
    assert "tmp_to_disk" not in task_catalog.default_tasks("server", REAL_TASKS)


def test_the_task_runs_after_the_network_is_configured() -> None:
    # The task installs its own tools from the archive, so it must follow the
    # task that configures the resolver of the machine.
    names = [record.name for record in REAL_TASKS]
    assert names.index("dnsproxy_setup") < names.index("tmp_to_disk")
