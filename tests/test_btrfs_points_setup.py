"""Tests of the save point task: the point, its boot entry and the work copy.

The section decides what the user finds in the boot menu after a broken system,
so the tests cover the whole chain on a temporary machine image: the read-only
snapshot of the root, the writable copy of that snapshot, the entry with the
in-memory root, the setting that keeps the point out of the generated list and
the one menu rebuild that follows. They also cover what must never happen: an
existing point is not recreated, an existing work copy is not overwritten, and a
machine without btrfs or without a mounted points subvolume is reported instead
of being changed.

No test touches the machine: the mount point, the fstab, the configuration of
the generator and the target of the entry are temporary files, and the commands
are recorded instead of run (docs/guards/testing-guide.md).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara.context import Context
from pyntara.tasks import btrfs_points_setup
from pyntara.values import btrfs_points_setup as values
from pyntara.values import btrfs_recompress as recompress_values
from pyntara.values import btrfs_setup as setup_values
from pyntara.values import tasks as tasks_values

# Root of the clone the tests run from, so the shipped templates are the ones
# the tests render.
REPO_ROOT = Path(__file__).resolve().parents[1]

# The two kernels of the fixture point, in the order a boot menu should show
# them: the newer version first, and the older one with its version in the name.
NEWER_KERNEL = "7.0.0-34-generic"
OLDER_KERNEL = "7.0.0-9-generic"

BTRFS_ROOT_ANSWER = "/dev/vda2[/@] btrfs rw,compress=zstd:15\n"
EXT4_ROOT_ANSWER = "/dev/vda2 ext4 rw,relatime\n"

# The fstab of the fixture machine, as a machine names its root device.
FSTAB_TEXT = "UUID=ec3f8aa4-98ba-4b28-85de-0c4dbff9f669 / btrfs defaults 0 0\n"

# The configuration file of the generator, with a setting of an earlier run.
GENERATOR_CONFIG_TEXT = (
    "# a comment\n"
    'GRUB_BTRFS_IGNORE_PREFIX_PATH=("var/lib/docker")\n'
    'GRUB_BTRFS_IGNORE_SPECIFIC_PATH=("@" "@points/Old-point")\n'
)

def _ctx(*, force: bool = False) -> Context:
    """Context of the task, with the force mode it was asked for."""

    return make_context(
        task_name="btrfs_points_setup",
        repo_root=REPO_ROOT,
        force_tasks=frozenset({"btrfs_points_setup"}) if force else frozenset(),
    )


class _Machine:
    """The temporary machine image the section works on."""

    def __init__(self, tmp_path: Path) -> None:
        self.mount_point = tmp_path / "points"
        self.mount_point.mkdir(parents=True, exist_ok=True)
        self.fstab = tmp_path / "fstab"
        self.fstab.write_text(FSTAB_TEXT, encoding="utf-8")
        self.generator_config = tmp_path / "grub-btrfs-config"
        self.generator_config.write_text(GENERATOR_CONFIG_TEXT, encoding="utf-8")
        self.entry = tmp_path / "40_pyntara_permanent_entry"
        self.calls: list[list[str]] = []
        self.subvolumes: set[Path] = set()

    @property
    def point(self) -> Path:
        return self.mount_point / values.POINT_NAME

    @property
    def work_copy(self) -> Path:
        return self.mount_point / values.WORK_COPY_NAME

    def store(self, directory: Path, *kernels: str) -> None:
        """Lay out the files of a subvolume, with the given kernels.

        The files and the subvolume itself are two facts on purpose: the
        section reads the files of a point it is about to store, and asks the
        tool whether the path already carries a subvolume, so a test that lays
        out files and a test that means the subvolume is already stored are
        written differently.
        """

        boot = directory / values.BOOT_DIRECTORY_NAME
        boot.mkdir(parents=True, exist_ok=True)
        for kernel in kernels:
            (boot / f"{values.KERNEL_FILE_PREFIX}{kernel}").write_text(
                "", encoding="utf-8"
            )
            (boot / f"{values.INITRD_FILE_PREFIX}{kernel}").write_text(
                "", encoding="utf-8"
            )

    def already_stored(self, *directories: Path) -> None:
        """Mark directories as subvolumes of the machine image."""

        for directory in directories:
            self.subvolumes.add(directory)


def _use_values(monkeypatch: pytest.MonkeyPatch, machine: _Machine) -> None:
    """Point the section at the temporary machine image."""

    monkeypatch.setattr(setup_values, "POINTS_MOUNT_POINT", machine.mount_point)
    monkeypatch.setattr(setup_values, "FSTAB_PATH", machine.fstab)
    monkeypatch.setattr(setup_values, "GRUB_BTRFS_CONFIG_PATH", machine.generator_config)
    monkeypatch.setattr(values, "GRUB_D_ENTRY_PATH", machine.entry)
    monkeypatch.setattr(btrfs_points_setup, "_points_mounted", lambda: True)


def _commands_fake(
    monkeypatch: pytest.MonkeyPatch,
    machine: _Machine,
    *,
    root_answer: str = BTRFS_ROOT_ANSWER,
    read_only_answer: str = "ro=true\n",
    snapshot_rc: int = 0,
    snapshot_error: str = "ERROR: Could not create subvolume: File exists",
    point_kernels: tuple[str, ...] = (NEWER_KERNEL,),
    kernels_without_initrd: tuple[str, ...] = (),
    active_jobs: tuple[bool, ...] = (),
) -> list[list[str]]:
    """Answer every command of the section; record the calls.

    findmnt answers the mount line, the subvolume question answers from the
    subvolumes of the machine image, the property query answers with the
    read-only state, the snapshot answers with snapshot_rc and a message that
    names the cause of a failure, and systemd answers with the state of its
    units. A snapshot of the root lays the kernels of the machine into the
    stored subvolume, exactly as the real snapshot carries the whole root, and
    a kernel named in kernels_without_initrd arrives without its ramdisk. The
    wait for the recompression job reads the given answers in turn and reports
    the job as finished after them.
    """

    calls = machine.calls
    answers = list(active_jobs)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        rc = 0
        stdout = ""
        stderr = ""
        if command[0] == "findmnt":
            stdout = root_answer
        elif command[0] == "btrfs" and command[1:3] == ["subvolume", "show"]:
            if Path(command[-1]) in machine.subvolumes:
                stdout = f"{command[-1]}\n        Name: {Path(command[-1]).name}\n"
            else:
                rc = 1
                stderr = "ERROR: not a subvolume\n"
        elif command[0] == "btrfs" and command[1:3] == ["property", "get"]:
            stdout = read_only_answer
        elif command[0] == "btrfs" and command[1:3] == ["subvolume", "snapshot"]:
            rc = snapshot_rc
            if rc != 0:
                stderr = snapshot_error
            else:
                target = Path(command[-1])
                machine.subvolumes.add(target)
                machine.store(target, *point_kernels)
                for kernel in kernels_without_initrd:
                    (target / values.BOOT_DIRECTORY_NAME / (
                        f"{values.INITRD_FILE_PREFIX}{kernel}"
                    )).unlink(missing_ok=True)
        if rc != 0 and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, command, stdout, stderr)
        return _FakeProc(rc, stdout, stderr)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    if active_jobs:
        monkeypatch.setattr(
            btrfs_points_setup,
            "service_is_active",
            lambda unit, timeout: answers.pop(0) if answers else False,
        )
    else:
        monkeypatch.setattr(
            btrfs_points_setup, "service_is_active", lambda unit, timeout: False
        )
    return calls


def test_points_setup_skips_a_machine_that_does_not_run_on_btrfs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine without btrfs keeps working without a recovery point, and the
    # task reports it as a warning of a completed task.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    calls = _commands_fake(monkeypatch, machine, root_answer=EXT4_ROOT_ANSWER)

    result = btrfs_points_setup.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert result.warnings and "ext4" in result.warnings[0]
    assert [call[0] for call in calls] == ["findmnt"]


def test_points_setup_skips_a_machine_without_a_mounted_points_subvolume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Storing a point into a directory that is not the points subvolume would
    # put the point inside the root subvolume, where it would travel away with
    # the root; the task reports it instead.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine)
    monkeypatch.setattr(btrfs_points_setup, "_points_mounted", lambda: False)

    result = btrfs_points_setup.task(_ctx())

    assert result.changed is False
    assert result.warnings and str(machine.mount_point) in result.warnings[0]


def test_points_setup_stores_the_point_and_the_work_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The point is a read-only snapshot of the running root, and the work copy
    # is a writable snapshot of the point: exactly these two commands, in this
    # order.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    calls = _commands_fake(monkeypatch, machine)

    result = btrfs_points_setup.task(_ctx())

    assert result.success is True
    assert result.changed is True
    snapshots = [
        call for call in calls if call[0] == "btrfs" and call[1:3] == ["subvolume", "snapshot"]
    ]
    assert snapshots[0] == [
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        str(setup_values.ROOT_MOUNT_POINT),
        str(machine.point),
    ]
    assert snapshots[1] == [
        "btrfs",
        "subvolume",
        "snapshot",
        str(machine.point),
        str(machine.work_copy),
    ]


def test_points_setup_writes_the_boot_entry_with_the_in_memory_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The entry is what the user chooses after a broken system, so it carries
    # the in-memory root, the device of the fstab, the point as the subvolume
    # and the newest kernel under the plain name of the point.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(
        monkeypatch, machine, point_kernels=(NEWER_KERNEL, OLDER_KERNEL)
    )

    btrfs_points_setup.task(_ctx())

    entry = machine.entry.read_text(encoding="utf-8")
    assert machine.entry.stat().st_mode & 0o777 == values.GRUB_D_ENTRY_FILE_MODE
    assert "exec tail -n +3 $0" in entry
    assert (
        "search --no-floppy --fs-uuid --set=root "
        "ec3f8aa4-98ba-4b28-85de-0c4dbff9f669" in entry
    )
    assert (
        f'root=UUID=ec3f8aa4-98ba-4b28-85de-0c4dbff9f669 ro '
        f"rootflags=subvol=@points/{values.POINT_NAME} "
        f"{values.OVERLAY_PARAMETER} quiet splash" in entry
    )
    assert (
        f'linux "/@points/{values.POINT_NAME}/boot/{values.KERNEL_FILE_PREFIX}{NEWER_KERNEL}"'
        in entry
    )
    assert (
        f'initrd "/@points/{values.POINT_NAME}/boot/{values.INITRD_FILE_PREFIX}{NEWER_KERNEL}"'
        in entry
    )
    assert f"menuentry '{values.POINT_NAME}'" in entry
    assert f"menuentry '{values.POINT_NAME} ({OLDER_KERNEL})'" in entry
    assert entry.index(NEWER_KERNEL) < entry.index(f"({OLDER_KERNEL})")


def test_points_setup_leaves_out_a_kernel_without_its_ramdisk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An entry without an initial ramdisk cannot boot, so the kernel gets no
    # entry and the section says which kernel it left out.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(
        monkeypatch,
        machine,
        point_kernels=(NEWER_KERNEL, OLDER_KERNEL),
        kernels_without_initrd=(OLDER_KERNEL,),
    )

    result = btrfs_points_setup.task(_ctx())

    assert OLDER_KERNEL not in machine.entry.read_text(encoding="utf-8")
    assert any(OLDER_KERNEL in warning for warning in result.warnings)


def test_points_setup_keeps_the_point_out_of_the_generated_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The point appears in the menu exactly once, as the entry of this section,
    # and the entries of an earlier run survive the edit.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine)

    btrfs_points_setup.task(_ctx())

    line = next(
        line
        for line in machine.generator_config.read_text(encoding="utf-8").splitlines()
        if line.startswith(values.GRUB_BTRFS_IGNORE_KEY)
    )
    assert line == (
        f'{values.GRUB_BTRFS_IGNORE_KEY}=("@" "@points/Old-point" '
        f'"@points/{values.POINT_NAME}")'
    )


def test_points_setup_ignores_a_commented_setting_of_the_generator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A commented line is an example of the generator, not a setting in force,
    # so the point is written into a line of its own.
    machine = _Machine(tmp_path)
    machine.generator_config.write_text(
        f'#{values.GRUB_BTRFS_IGNORE_KEY}=("example")\n', encoding="utf-8"
    )
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine)

    btrfs_points_setup.task(_ctx())

    text = machine.generator_config.read_text(encoding="utf-8")
    assert f'#{values.GRUB_BTRFS_IGNORE_KEY}=("example")' in text
    assert (
        f'{values.GRUB_BTRFS_IGNORE_KEY}=("@points/{values.POINT_NAME}")' in text
    )


def test_points_setup_rebuilds_the_menu_with_the_generator_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A rebuild that races the generator can leave the generated file missing,
    # so the generator is stopped for the moment of the rebuild and started
    # again afterwards.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    calls = _commands_fake(monkeypatch, machine)

    btrfs_points_setup.task(_ctx())

    order = [call[0] for call in calls]
    stop = order.index("systemctl")
    assert calls[stop][1:] == [
        "stop",
        setup_values.GRUB_BTRFS_DAEMON_UNIT_NAME,
    ]
    assert order.index("update-grub") > stop
    assert calls[-1][1:] == [
        "start",
        setup_values.GRUB_BTRFS_DAEMON_UNIT_NAME,
    ]


def test_points_setup_changes_nothing_on_a_machine_that_already_carries_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A rerun finds the point, the work copy, the entry and the setting in
    # place: it stores nothing, rewrites nothing and does not spend minutes on
    # a menu rebuild.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    machine.store(machine.point, NEWER_KERNEL)
    machine.store(machine.work_copy, NEWER_KERNEL)
    machine.generator_config.write_text(
        f'{values.GRUB_BTRFS_IGNORE_KEY}=("@points/{values.POINT_NAME}")\n',
        encoding="utf-8",
    )
    _commands_fake(monkeypatch, machine)
    machine.already_stored(machine.point, machine.work_copy)
    btrfs_points_setup._write_boot_entry(_ctx(), [])

    result = btrfs_points_setup.task(_ctx())

    assert result.changed is False
    assert not [
        call for call in machine.calls if call[0] in {"update-grub"} or call[1:3] == ["subvolume", "snapshot"]
    ]


def test_points_setup_rebuilds_the_menu_when_the_run_forces_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A kernel that changed inside a work copy does not reach the menu through
    # the generator, so the forced run is the way to refresh it by hand.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    machine.store(machine.point, NEWER_KERNEL)
    machine.store(machine.work_copy, NEWER_KERNEL)
    machine.generator_config.write_text(
        f'{values.GRUB_BTRFS_IGNORE_KEY}=("@points/{values.POINT_NAME}")\n',
        encoding="utf-8",
    )
    calls = _commands_fake(monkeypatch, machine)
    machine.already_stored(machine.point, machine.work_copy)
    btrfs_points_setup._write_boot_entry(_ctx(), [])

    result = btrfs_points_setup.task(_ctx(force=True))

    assert result.changed is False
    assert any(call[0] == "update-grub" for call in calls)


def test_points_setup_reports_a_point_that_is_not_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A point that can be written is not a point: a session started from it
    # would change the state the user returns to.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    machine.store(machine.point, NEWER_KERNEL)
    _commands_fake(monkeypatch, machine, read_only_answer="ro=false\n")
    machine.already_stored(machine.point, machine.work_copy)

    result = btrfs_points_setup.task(_ctx())

    assert result.warnings
    assert any("not read only" in warning for warning in result.warnings)


def test_points_setup_reports_a_snapshot_that_cannot_be_stored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine without room for the snapshot is reported, and no entry is
    # written for a point that is not there.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine, snapshot_rc=1)

    result = btrfs_points_setup.task(_ctx())

    assert result.warnings
    assert any("could not be stored" in warning for warning in result.warnings)
    assert any("File exists" in warning for warning in result.warnings)
    assert machine.entry.exists() is False


def test_points_setup_reports_a_directory_that_occupies_the_place_of_the_point(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A snapshot into an existing directory is stored inside that directory
    # under another name, where nobody looks for it, so the section refuses the
    # path and says what to move aside instead of storing something invisible.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    machine.store(machine.point, NEWER_KERNEL)
    calls = _commands_fake(monkeypatch, machine)

    result = btrfs_points_setup.task(_ctx())

    assert any("carries no subvolume" in warning for warning in result.warnings)
    assert not [
        call for call in calls if call[1:3] == ["subvolume", "snapshot"]
    ]


def test_points_setup_waits_for_the_recompression_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The point should carry the finished state of the machine, so the section
    # waits while the one-off recompression of the storage section runs.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine, active_jobs=(True, True, False))
    monkeypatch.setattr(recompress_values, "JOB_WAIT_POLL_SECONDS", 0.0)

    result = btrfs_points_setup.task(_ctx())

    assert any(
        call[1:3] == ["subvolume", "snapshot"] for call in machine.calls
    )
    assert result.success is True


def test_points_setup_stores_the_point_when_the_job_never_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The wait is bounded: a machine whose job runs longer still receives its
    # point, taken from the state it has by then, and the user is told.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    machine.store(machine.point, NEWER_KERNEL)
    _commands_fake(monkeypatch, machine, active_jobs=(True, True, True))
    monkeypatch.setattr(recompress_values, "JOB_WAIT_POLL_SECONDS", 0.0)
    monkeypatch.setattr(recompress_values, "JOB_WAIT_LIMIT_SECONDS", 0)

    result = btrfs_points_setup.task(_ctx())

    assert result.success is True
    assert any("still runs" in warning for warning in result.warnings)


def test_points_setup_does_not_wait_when_the_point_is_already_stored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The wait exists because the point should carry the finished state of the
    # machine; a machine that carries its point has nothing to take, so a rerun
    # runs while the one-off recompression of another run is still going.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    machine.store(machine.point, NEWER_KERNEL)
    machine.store(machine.work_copy, NEWER_KERNEL)
    _commands_fake(monkeypatch, machine)
    machine.already_stored(machine.point, machine.work_copy)
    monkeypatch.setattr(
        btrfs_points_setup,
        "service_is_active",
        lambda unit, timeout: pytest.fail("the section waited for the job"),
    )

    result = btrfs_points_setup.task(_ctx())

    assert result.success is True


def test_points_setup_reads_the_device_of_the_fstab_for_its_search_line() -> None:
    # The entry names the device the way this machine names it, so a machine
    # that names it by label or by its device path works as well.
    uuid_line = btrfs_points_setup._search_line("UUID=abc")

    assert uuid_line == "search --no-floppy --fs-uuid --set=root abc"
    assert (
        btrfs_points_setup._search_line("LABEL=root")
        == "search --no-floppy --label --set=root root"
    )
    assert (
        btrfs_points_setup._search_line("/dev/vda2")
        == "search --no-floppy --set=root /dev/vda2"
    )


def test_points_setup_orders_kernel_versions_by_their_numbers() -> None:
    # A text comparison would put 7.0.0-9 after 7.0.0-34, and the plain title
    # of the point would then belong to an older kernel.
    versions = ["7.0.0-9-generic", "7.0.0-34-generic", "6.8.0-11-generic"]
    ordered = sorted(
        versions, key=btrfs_points_setup._kernel_version_key, reverse=True
    )

    assert ordered == ["7.0.0-34-generic", "7.0.0-9-generic", "6.8.0-11-generic"]


def test_points_setup_depends_on_the_two_storage_sections() -> None:
    # The point is taken from the finished filesystem, so the section runs
    # after the storage setup and after the one-off recompression.
    records = {record.name: record for record in tasks_values.CATALOG}

    assert set(records["btrfs_points_setup"].depends) == {
        "btrfs_setup",
        "btrfs_recompress",
    }


def test_points_setup_belongs_to_every_install_mode() -> None:
    # A server receives a recovery point as well; it is the only state a
    # machine can be returned to.
    records = {record.name: record for record in tasks_values.CATALOG}

    assert records["btrfs_points_setup"].modes == tasks_values.MODES


def test_the_shipped_entry_templates_carry_the_placeholders_the_task_fills() -> None:
    # A renamed placeholder would render as an empty string in a boot menu,
    # where nobody can repair it, so the shipping templates are checked here.
    template_dir = REPO_ROOT / "task_data" / "btrfs_points_setup"
    header = (template_dir / values.GRUB_D_ENTRY_HEADER_FILE_NAME).read_text(
        encoding="utf-8"
    )
    body = (template_dir / values.GRUB_D_ENTRY_BODY_FILE_NAME).read_text(
        encoding="utf-8"
    )

    assert header.startswith("#!")
    assert "exec tail -n +3 $0" in header
    for placeholder in (
        "menu_title",
        "entry_id",
        "entry_class",
        "search_line",
        "kernel_path",
        "root_spec",
        "subvolume",
        "overlay_parameter",
        "initrd_path",
    ):
        assert re.search(rf"\${placeholder}\b", body), placeholder
