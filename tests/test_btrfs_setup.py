"""Tests of the storage setup task: compression, points, timer and menu.

The section brings a fresh machine to the state the rest of the run expects, so
the tests cover the whole chain on a temporary machine image: the packages of
the section, the compression option on the fstab lines of the mounted
subvolumes, the points subvolume with its mount, the maintenance schedule with
the state of its timers, and the generator of menu entries with its settings and
its daemon.

Two kinds of test carry the weight here. The first is the rerun: every step
reads the state before it writes, so a second run on a configured machine must
report that it changed nothing and must not run a single apt, mount or build
command. The second is the machine that fails one step: the section reports it
as a warning of a completed task and keeps the remaining steps and tasks
running.

No test touches the machine: the fstab, the configuration files, the build
directory and the mount points are temporary paths, and the commands are
recorded instead of run (docs/guards/testing-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara.context import Context
from pyntara.tasks import btrfs_setup
from pyntara.values import btrfs_setup as values
from pyntara.values import tasks as tasks_values

# Root of the clone the tests run from, so the shipped template is the one the
# tests render.
REPO_ROOT = Path(__file__).resolve().parents[1]

# Package set of the tests: the shipped list is long and every name would need
# its own answer from the package manager, while the install path is the same
# for one name and for ten.
TEST_PACKAGES = ("btrfs-progs",)

BTRFS_ROOT_ANSWER = "/dev/vda2[/@] btrfs rw,noatime,subvolid=256\n"
EXT4_ROOT_ANSWER = "/dev/vda2 ext4 rw,relatime\n"

# The fstab of a fresh machine: the root and the home of the running system.
FSTAB_TEXT = (
    "UUID=ec3f8aa4-98ba-4b28-85de-0c4dbff9f669 / btrfs rw,noatime 0 0\n"
    "UUID=ec3f8aa4-98ba-4b28-85de-0c4dbff9f669 /home btrfs rw,noatime 0 0\n"
)

# What the maintenance package ships: the lines of the section carry other
# values, so the run has to write the declared ones.
MAINTENANCE_TEXT = (
    'BTRFS_LOG_OUTPUT=""\n'
    'BTRFS_DEFRAG_PERIOD="weekly"\n'
    'BTRFS_SCRUB_PERIOD="monthly"\n'
)

# What the generator ships as its configuration.
GENERATOR_CONFIG_TEXT = (
    "# a comment of the generator\n"
    'GRUB_BTRFS_IGNORE_PREFIX_PATH=("var/lib/docker")\n'
    'GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS="overlayroot=tmpfs"\n'
)

SUBDIR_ANSWER_WITHOUT_POINTS = "ID 256 gen 10 top level 5 path @\n"
SUBDIR_ANSWER_WITH_POINTS = "ID 256 gen 10 top level 5 path @\nID 263 gen 12 top level 5 path @points\n"


class _Machine:
    """The temporary machine image the section works on."""

    def __init__(self, tmp_path: Path) -> None:
        self.fstab = tmp_path / "fstab"
        self.fstab.write_text(FSTAB_TEXT, encoding="utf-8")
        self.maintenance = tmp_path / "btrfsmaintenance"
        self.maintenance.write_text(MAINTENANCE_TEXT, encoding="utf-8")
        self.generator_config = tmp_path / "grub-btrfs-config"
        self.generator_config.write_text(GENERATOR_CONFIG_TEXT, encoding="utf-8")
        self.build_directory = tmp_path / "build"
        self.generator = tmp_path / "41_snapshots-btrfs"
        self.dropin = tmp_path / "grub-btrfsd.service.d" / "watch-points.conf"
        self.points_mount_point = tmp_path / "points"
        self.toplevel_mount_point = tmp_path / "toplevel"
        self.installed_packages: set[str] = set()
        self.enabled_timers: set[str] = set()
        self.calls: list[list[str]] = []
        self.mount_rc = 0
        self.build_rc = 0

    def calls_of(self, program: str) -> list[list[str]]:
        """Every recorded call of one program."""

        return [call for call in self.calls if call[0] == program]


def _ctx(*, skip_apt_update: bool = False) -> Context:
    """Context of the task."""

    return make_context(
        task_name="btrfs_setup",
        repo_root=REPO_ROOT,
        skip_apt_update=skip_apt_update,
    )


def _use_values(monkeypatch: pytest.MonkeyPatch, machine: _Machine) -> None:
    """Point every path of the section at the temporary machine image."""

    monkeypatch.setattr(values, "PACKAGES", TEST_PACKAGES)
    monkeypatch.setattr(values, "FSTAB_PATH", machine.fstab)
    monkeypatch.setattr(values, "MAINTENANCE_CONFIG_PATH", machine.maintenance)
    monkeypatch.setattr(values, "GRUB_BTRFS_CONFIG_PATH", machine.generator_config)
    monkeypatch.setattr(values, "GRUB_BTRFS_BUILD_DIRECTORY", machine.build_directory)
    monkeypatch.setattr(values, "GRUB_BTRFS_INSTALLED_PATH", machine.generator)
    monkeypatch.setattr(values, "GRUB_BTRFS_DAEMON_DROPIN_PATH", machine.dropin)
    monkeypatch.setattr(values, "POINTS_MOUNT_POINT", machine.points_mount_point)
    monkeypatch.setattr(values, "TOPLEVEL_MOUNT_POINT", machine.toplevel_mount_point)


def _commands_fake(
    monkeypatch: pytest.MonkeyPatch,
    machine: _Machine,
    *,
    root_answer: str = BTRFS_ROOT_ANSWER,
    subvolume_answer: str = SUBDIR_ANSWER_WITHOUT_POINTS,
    install_rc: int = 0,
    mount_rc: int | None = None,
    build_rc: int | None = None,
) -> list[list[str]]:
    """Answer every command of the section and record the calls.

    The package manager answers from the set of installed packages of the
    machine image, findmnt answers the mount line, the subvolume listing answers
    the subvolumes the machine carries, systemctl answers the state of its
    timers, and the build of the generator creates the file the build installs.
    """

    calls = machine.calls
    mount_failure = machine.mount_rc if mount_rc is None else mount_rc
    build_failure = machine.build_rc if build_rc is None else build_rc

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        rc = 0
        stdout = ""
        if command[0] == "findmnt":
            stdout = root_answer
        elif command[0] == "dpkg-query":
            if command[-1] not in machine.installed_packages:
                rc = 1
            else:
                stdout = "install ok installed\n"
        elif command[0] == "apt-get" and command[1] == "install":
            if install_rc == 0:
                machine.installed_packages.update(
                    command[command.index("install") + 1 :]
                )
            rc = install_rc
        elif command[0] == "btrfs" and command[1:3] == ["subvolume", "list"]:
            stdout = subvolume_answer
        elif command[0] in {"mount", "umount"}:
            rc = mount_failure
        elif command[0] == "make":
            if build_failure == 0:
                machine.generator.write_text("#!/bin/sh\n", encoding="utf-8")
            rc = build_failure
        elif command[0] == "systemctl":
            unit = command[-1]
            if command[1] == "is-enabled":
                stdout = "enabled\n" if unit in machine.enabled_timers else "disabled\n"
                rc = 0 if unit in machine.enabled_timers else 1
            elif command[1] == "enable":
                machine.enabled_timers.add(unit)
            elif command[1] == "disable":
                machine.enabled_timers.discard(unit)
        if rc != 0 and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, command, stdout)
        return _FakeProc(rc, stdout)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _machine_that_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> _Machine:
    """A machine image that already carries everything the section writes."""

    machine = _Machine(tmp_path)
    machine.installed_packages.update(TEST_PACKAGES)
    machine.generator.write_text("#!/bin/sh\n", encoding="utf-8")
    machine.enabled_timers.update(values.MAINTENANCE_ENABLED_TIMERS)
    machine.enabled_timers.add(values.GRUB_BTRFS_DAEMON_UNIT_NAME)
    machine.fstab.write_text(
        "UUID=ec3f8aa4-98ba-4b28-85de-0c4dbff9f669 / btrfs "
        f"{values.COMPRESSION_OPTION_ASSIGNMENT},noatime 0 0\n"
        "UUID=ec3f8aa4-98ba-4b28-85de-0c4dbff9f669 /home btrfs "
        f"{values.COMPRESSION_OPTION_ASSIGNMENT},noatime 0 0\n"
        f"UUID=ec3f8aa4-98ba-4b28-85de-0c4dbff9f669 {machine.points_mount_point} "
        f"btrfs subvol={values.POINTS_SUBVOLUME_NAME},defaults 0 0\n",
        encoding="utf-8",
    )
    directives = "\n".join(values.MAINTENANCE_DIRECTIVES) + "\n"
    machine.maintenance.write_text(directives, encoding="utf-8")
    machine.generator_config.write_text(
        GENERATOR_CONFIG_TEXT.replace(
            'GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS="overlayroot=tmpfs"',
            values.GRUB_BTRFS_KERNEL_PARAMETERS_DIRECTIVE,
        ),
        encoding="utf-8",
    )
    return machine


def test_btrfs_setup_skips_a_machine_that_does_not_run_on_btrfs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine with another filesystem keeps working without compressed
    # storage, and the task reports it as a warning of a completed task.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    calls = _commands_fake(monkeypatch, machine, root_answer=EXT4_ROOT_ANSWER)

    result = btrfs_setup.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert result.warnings and "ext4" in result.warnings[0]
    assert [call[0] for call in calls] == ["findmnt"]


def test_btrfs_setup_reports_a_root_filesystem_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine whose findmnt answers nothing is reported, and nothing is
    # changed on a machine the section does not understand.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine, root_answer="")

    result = btrfs_setup.task(_ctx())

    assert result.changed is False
    assert result.warnings and "could not be read" in result.warnings[0]


def test_btrfs_setup_brings_a_fresh_machine_to_the_declared_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The whole chain of the section on a fresh machine: the package, the
    # compression on both mount points, the points subvolume with its line and
    # its mount, the maintenance schedule, the generator and its settings.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine)
    monkeypatch.setattr(btrfs_setup, "_points_mounted", lambda: False)

    result = btrfs_setup.task(_ctx())

    assert result.success is True
    assert result.changed is True

    fstab = machine.fstab.read_text(encoding="utf-8")
    assert fstab.count(values.COMPRESSION_OPTION_ASSIGNMENT) == 2
    assert f"{machine.points_mount_point}" in fstab
    assert f"subvol={values.POINTS_SUBVOLUME_NAME}" in fstab

    created = [
        call
        for call in machine.calls_of("btrfs")
        if call[1:4]
        == ["subvolume", "create", str(machine.toplevel_mount_point / values.POINTS_SUBVOLUME_NAME)]
    ]
    assert created
    mounts = [call[1:] for call in machine.calls_of("mount")]
    assert ["-o", f"subvolid={values.TOPLEVEL_SUBVOLUME_ID}", "/dev/vda2", str(machine.toplevel_mount_point)] in mounts
    assert [str(machine.points_mount_point)] in mounts
    assert ["-o", "remount", "/"] in mounts
    assert machine.calls_of("umount")

    maintenance = machine.maintenance.read_text(encoding="utf-8")
    for directive in values.MAINTENANCE_DIRECTIVES:
        assert directive in maintenance
    assert set(values.MAINTENANCE_ENABLED_TIMERS) <= machine.enabled_timers
    for timer in values.MAINTENANCE_DISABLED_TIMERS:
        assert not any(
            call[1:3] == ["enable", "--now"] and call[-1] == timer
            for call in machine.calls_of("systemctl")
        )

    assert machine.generator.is_file()
    assert machine.calls_of("make")
    generator_config = machine.generator_config.read_text(encoding="utf-8")
    assert values.GRUB_BTRFS_KERNEL_PARAMETERS_DIRECTIVE in generator_config
    assert "# a comment of the generator" in generator_config
    assert machine.dropin.is_file()
    assert any(call[1] == "daemon-reload" for call in machine.calls_of("systemctl"))
    assert any(
        call[1:3] == ["enable", "--now"]
        and call[-1] == values.GRUB_BTRFS_DAEMON_UNIT_NAME
        for call in machine.calls_of("systemctl")
    )


def test_btrfs_setup_changes_nothing_on_a_machine_that_is_already_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A rerun on a configured machine is cheap: no install, no mount, no build,
    # and the answer says that nothing changed.
    machine = _machine_that_changes_nothing(monkeypatch, tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine, subvolume_answer=SUBDIR_ANSWER_WITH_POINTS)
    monkeypatch.setattr(btrfs_setup, "_points_mounted", lambda: True)
    btrfs_setup._deploy_daemon_dropin(_ctx(), [])

    result = btrfs_setup.task(_ctx(skip_apt_update=True))

    assert result.success is True
    assert result.changed is False
    assert machine.calls_of("dpkg-query")
    assert machine.calls_of("apt-get") == []
    assert machine.calls_of("mount") == []
    assert machine.calls_of("make") == []


def test_btrfs_setup_reports_a_points_subvolume_that_cannot_be_mounted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The points hold the recovery point, so a machine whose points cannot be
    # mounted is told about it while the remaining steps still run.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine, mount_rc=1)
    monkeypatch.setattr(btrfs_setup, "_points_mounted", lambda: False)

    result = btrfs_setup.task(_ctx())

    assert result.success is True
    assert result.warnings
    assert any("could not be mounted" in warning for warning in result.warnings)
    assert machine.maintenance.read_text(encoding="utf-8") != MAINTENANCE_TEXT


def test_btrfs_setup_reports_a_generator_that_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without the generator the menu shows no points, and the machine is still
    # a working machine.
    machine = _Machine(tmp_path)
    _use_values(monkeypatch, machine)
    _commands_fake(monkeypatch, machine, build_rc=1)
    monkeypatch.setattr(btrfs_setup, "_points_mounted", lambda: True)

    result = btrfs_setup.task(_ctx())

    assert result.success is True
    assert result.warnings
    assert any("menu generator" in warning for warning in result.warnings)


def test_btrfs_setup_writes_the_compression_option_on_the_mounted_subvolumes() -> None:
    # The compression of the machine is one fact, and the option the fstab
    # receives is the option the recompression section rewrites with.
    assert values.COMPRESSION_OPTION_ASSIGNMENT.startswith("compress=")
    assert values.COMPRESSED_MOUNT_POINTS == ("/", "/home")


def test_btrfs_setup_depends_on_the_extra_repositories() -> None:
    # The generator is built from sources and its tools come from the extra
    # archive components, so the section runs after the repositories are on.
    records = {record.name: record for record in tasks_values.CATALOG}

    assert "add_extra_repos" in records["btrfs_setup"].depends


def test_btrfs_setup_belongs_to_every_install_mode() -> None:
    # Every machine gets the storage work, including the quick set.
    records = {record.name: record for record in tasks_values.CATALOG}

    assert records["btrfs_setup"].modes == tasks_values.MODES


def test_the_shipped_daemon_dropin_carries_the_placeholders_the_task_fills() -> None:
    # The drop-in points the daemon at the points mount and orders it after
    # that mount, so the placeholders are the interface of the shipped file.
    text = (
        REPO_ROOT / "task_data" / "btrfs_setup" / values.GRUB_BTRFS_DAEMON_DROPIN_FILE_NAME
    ).read_text(encoding="utf-8")

    for placeholder in (
        "points_mount_point",
        "daemon_path",
        "syslog_option",
        "watch_option",
    ):
        assert f"${placeholder}" in text, placeholder
