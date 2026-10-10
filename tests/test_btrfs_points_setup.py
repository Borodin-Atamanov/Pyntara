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
from pyntara.values import swapfile_service_install as swapfile_values
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

# The device of the boot directory of the fixture machine and the path the boot
# loader's tool gives for a file there. The fixture machine keeps its kernels on
# a boot partition of its own, so the tool names a file at the root of that
# partition and the device is the partition and not the root filesystem.
BOOT_UUID = "f2b69b19-4046-44e9-afed-6f890355ea62"
BOOT_RELPATH_PREFIX = "/"

# Package set of the tests: the shipped list names grub2-common, and the install
# path is the same for every package, so one name keeps the fixture small.
TEST_PACKAGES = ("grub2-common",)

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
        self.boot = tmp_path / "boot"
        self.boot.mkdir(parents=True, exist_ok=True)
        self.installed_packages: set[str] = set()
        self.calls: list[list[str]] = []
        self.subvolumes: set[Path] = set()

    @property
    def point(self) -> Path:
        return self.mount_point / values.POINT_NAME

    @property
    def work_copy(self) -> Path:
        return self.mount_point / values.WORK_COPY_NAME

    def install_kernels(
        self, *kernels: str, without_initrd: tuple[str, ...] = ()
    ) -> None:
        """Lay out the kernels of the machine in its boot directory.

        The section reads the kernels of the machine from this directory and not
        from inside the point, so a test lays them out here; a kernel named in
        without_initrd arrives without its ramdisk.
        """

        self.boot.mkdir(parents=True, exist_ok=True)
        for kernel in kernels:
            (self.boot / f"{values.KERNEL_FILE_PREFIX}{kernel}").write_text(
                "", encoding="utf-8"
            )
            if kernel in without_initrd:
                continue
            (self.boot / f"{values.INITRD_FILE_PREFIX}{kernel}").write_text(
                "", encoding="utf-8"
            )

    def place_directory(self, directory: Path) -> None:
        """Create a plain directory where the section expects a subvolume."""

        directory.mkdir(parents=True, exist_ok=True)

    def already_stored(self, *directories: Path) -> None:
        """Mark directories as subvolumes of the machine image."""

        for directory in directories:
            self.subvolumes.add(directory)


def _use_values(monkeypatch: pytest.MonkeyPatch, machine: _Machine) -> None:
    """Point the section at the temporary machine image."""

    monkeypatch.setattr(values, "PACKAGES", TEST_PACKAGES)
    monkeypatch.setattr(values, "BOOT_MOUNT_POINT", machine.boot)
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
    machine_kernels: tuple[str, ...] = (NEWER_KERNEL,),
    kernels_without_initrd: tuple[str, ...] = (),
    boot_relpath_prefix: str = BOOT_RELPATH_PREFIX,
    active_jobs: tuple[bool, ...] = (),
) -> list[list[str]]:
    """Answer every command of the section; record the calls.

    The package manager answers from the set of installed packages of the
    machine image, findmnt answers the mount line, the subvolume question
    answers from the subvolumes of the machine image, the property query
    answers with the read-only state, the snapshot answers with snapshot_rc and
    a message that names the cause of a failure, the two boot loader tools
    answer the device of the boot directory and the path of a file on it, and
    systemd answers with the state of its units. The kernels of the machine are
    laid out in its boot directory, exactly as the section reads them, and a
    kernel named in kernels_without_initrd arrives without its ramdisk. The wait
    for the recompression job reads the given answers in turn and reports the
    job as finished after them.
    """

    calls = machine.calls
    answers = list(active_jobs)
    machine.install_kernels(*machine_kernels, without_initrd=kernels_without_initrd)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        rc = 0
        stdout = ""
        stderr = ""
        if command[0] == "findmnt":
            stdout = root_answer
        elif command[0] == "dpkg-query":
            if command[-1] not in machine.installed_packages:
                rc = 1
            else:
                stdout = "install ok installed\n"
        elif command[0] == "apt-get" and command[1] == "install":
            machine.installed_packages.add(command[-1])
        elif command[0] == "grub-probe":
            stdout = f"{BOOT_UUID}\n"
        elif command[0] == "grub-mkrelpath":
            name = Path(command[-1]).name
            stdout = f"{boot_relpath_prefix.rstrip('/')}/{name}\n"
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
                machine.subvolumes.add(Path(command[-1]))
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


def test_the_snapshots_need_no_release_of_the_swap_of_the_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The storage section keeps the swap area in a subvolume of its own, and a
    # subvolume is a barrier for a snapshot: the point never carries the swap
    # file, the kernel refuses neither the snapshot nor the next activation of
    # the swap, and this section never stops the swap of the machine.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    calls = _commands_fake(monkeypatch, machine)
    monkeypatch.setattr(
        btrfs_points_setup,
        "service_is_active",
        lambda unit, timeout: unit == swapfile_values.SERVICE_UNIT_NAME,
    )

    result = btrfs_points_setup.task(_ctx())

    assert result.success is True
    assert not any("could not be stored" in warning for warning in result.warnings)
    assert not any(
        call[-1] == swapfile_values.SERVICE_UNIT_NAME for call in calls
    )


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


def test_points_setup_writes_the_boot_entries_of_both_snapshots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The section writes one group of entries per snapshot, one entry per kernel,
    # so the menu offers every kernel for the read-only snapshot and for the
    # writable one. The read-only group boots with the root filesystem in memory,
    # the writable group without it, and both name the subvolume of their own
    # snapshot.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(
        monkeypatch, machine, machine_kernels=(NEWER_KERNEL, OLDER_KERNEL)
    )

    btrfs_points_setup.task(_ctx())

    entry = machine.entry.read_text(encoding="utf-8")
    assert machine.entry.stat().st_mode & 0o777 == values.GRUB_D_ENTRY_FILE_MODE
    assert "exec tail -n +3 $0" in entry
    assert f"search --no-floppy --fs-uuid --set=root {BOOT_UUID}" in entry
    assert (
        f"root=UUID=ec3f8aa4-98ba-4b28-85de-0c4dbff9f669 ro "
        f"rootflags=subvol=@points/{values.POINT_NAME} "
        f"{values.OVERLAY_PARAMETER}" in entry
    )
    assert f"rootflags=subvol=@points/{values.WORK_COPY_NAME}" in entry
    assert entry.count(values.OVERLAY_PARAMETER) == 2
    assert f'linux "/{values.KERNEL_FILE_PREFIX}{NEWER_KERNEL}"' in entry
    assert f'initrd "/{values.INITRD_FILE_PREFIX}{NEWER_KERNEL}"' in entry


def test_points_setup_gives_every_menu_entry_a_name_of_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The name of an entry is both its identifier and its title, and the
    # identifier is the documented way to preselect an entry, so two entries that
    # share one make the choice depend on the order of the entries. The newest
    # kernel carries the word of the newest kernel and every older kernel carries
    # its version, with every character the boot loader refuses replaced by a
    # hyphen.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(
        monkeypatch, machine, machine_kernels=(NEWER_KERNEL, OLDER_KERNEL)
    )

    btrfs_points_setup.task(_ctx())

    entry = machine.entry.read_text(encoding="utf-8")
    identifiers = re.findall(r"--id (\S+)", entry)
    assert identifiers == [
        f"{values.GRUB_READ_ONLY_ENTRY_NAME}-{values.GRUB_LATEST_KERNEL_SUFFIX}",
        f"{values.GRUB_READ_ONLY_ENTRY_NAME}-7-0-0-9-generic",
        f"{values.GRUB_WRITABLE_ENTRY_NAME}-{values.GRUB_LATEST_KERNEL_SUFFIX}",
        f"{values.GRUB_WRITABLE_ENTRY_NAME}-7-0-0-9-generic",
    ]
    assert len(set(identifiers)) == len(identifiers)
    for identifier in identifiers:
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", identifier)
        assert f"menuentry '{identifier}'" in entry


def test_points_setup_replaces_a_stale_boot_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine that still carries an entry of an earlier layout must not keep
    # it: the section writes its own entry again, so the menu names the device
    # and the kernel of this machine and not of the machine the file came from.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    machine.entry.write_text(
        "#!/bin/sh\nexec tail -n +3 $0\n"
        "menuentry 'Pyntara-permanent' {\n"
        "    search --no-floppy --fs-uuid --set=root stale-uuid\n"
        "}\n",
        encoding="utf-8",
    )
    _commands_fake(monkeypatch, machine)

    btrfs_points_setup.task(_ctx())

    entry = machine.entry.read_text(encoding="utf-8")
    assert "stale-uuid" not in entry
    assert BOOT_UUID in entry


def test_points_setup_installs_the_boot_loader_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The section asks the boot loader's own tools for the device and the path of
    # a kernel, so it installs the package that carries them the same way every
    # other section installs its packages.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    calls = _commands_fake(monkeypatch, machine)

    result = btrfs_points_setup.task(_ctx())

    assert result.changed is True
    installs = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert installs
    assert installs[0][-1] in TEST_PACKAGES


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
        machine_kernels=(NEWER_KERNEL, OLDER_KERNEL),
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
        f'"@points/{values.POINT_NAME}" "@points/{values.WORK_COPY_NAME}")'
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
        f'{values.GRUB_BTRFS_IGNORE_KEY}='
        f'("@points/{values.POINT_NAME}" "@points/{values.WORK_COPY_NAME}")' in text
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
    machine.installed_packages.update(TEST_PACKAGES)
    machine.install_kernels(NEWER_KERNEL)
    machine.generator_config.write_text(
        f'{values.GRUB_BTRFS_IGNORE_KEY}='
        f'("@points/{values.POINT_NAME}" "@points/{values.WORK_COPY_NAME}")\n',
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
    machine.installed_packages.update(TEST_PACKAGES)
    machine.install_kernels(NEWER_KERNEL)
    machine.generator_config.write_text(
        f'{values.GRUB_BTRFS_IGNORE_KEY}='
        f'("@points/{values.POINT_NAME}" "@points/{values.WORK_COPY_NAME}")\n',
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
    machine.place_directory(machine.point)
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
    machine.place_directory(machine.point)
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
    machine.install_kernels(NEWER_KERNEL)
    _commands_fake(monkeypatch, machine)
    machine.already_stored(machine.point, machine.work_copy)
    monkeypatch.setattr(
        btrfs_points_setup,
        "service_is_active",
        lambda unit, timeout: pytest.fail("the section waited for the job"),
    )

    result = btrfs_points_setup.task(_ctx())

    assert result.success is True


def test_points_setup_takes_the_kernel_path_from_the_boot_loader_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The path is used exactly as the tool answered it, so the same code works on
    # a machine whose boot directory lives inside the root subvolume: there the
    # tool names the file through the subvolume, and the section must not rewrite
    # that.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(
        monkeypatch,
        machine,
        machine_kernels=(NEWER_KERNEL,),
        boot_relpath_prefix="/@/boot",
    )

    btrfs_points_setup.task(_ctx())

    entry = machine.entry.read_text(encoding="utf-8")
    assert f'linux "/@/boot/{values.KERNEL_FILE_PREFIX}{NEWER_KERNEL}"' in entry
    assert f'initrd "/@/boot/{values.INITRD_FILE_PREFIX}{NEWER_KERNEL}"' in entry


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
