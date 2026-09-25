"""Tests of the one-off recompression task and of its deployed program.

The program runs for minutes on a real filesystem, so it is tested twice: the
program itself runs in this suite with a stand-in for the btrfs tool, which
proves that the option names the task builds are the option names the program
reads, and the task is tested with the command runner faked, which covers the
decisions around the job: when the work is skipped, when it is forced, and what
the user is told.

No test touches the machine: the deployed paths, the marker and the mount points
are temporary files, and the commands are recorded instead of run
(docs/guards/testing-guide.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara.context import Context
from pyntara.tasks import btrfs_recompress
from pyntara.values import btrfs_recompress as values
from pyntara.values import btrfs_setup as setup_values
from pyntara.values import tasks as tasks_values

# Root of the clone the tests run from, so the real program is tested.
REPO_ROOT = Path(__file__).resolve().parents[1]

# The program that ships with the section.
PROGRAM_PATH = REPO_ROOT / "task_data" / "btrfs_recompress" / "recompress_btrfs.py"

# A findmnt answer of a btrfs root and of a machine without btrfs.
BTRFS_ROOT_ANSWER = "/dev/vda2[/@] btrfs rw,compress=zstd:15\n"
EXT4_ROOT_ANSWER = "/dev/vda2 ext4 rw,relatime\n"


def _ctx(*, force: bool = False) -> Context:
    """Context of the task, with the force mode it was asked for."""

    return make_context(
        task_name="btrfs_recompress",
        repo_root=REPO_ROOT,
        force_tasks=frozenset({"btrfs_recompress"}) if force else frozenset(),
    )


def _use_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    minimum_free_gib: int = 0,
) -> Path:
    """Point the deployed paths at the temporary tree; return the marker path.

    The program is read from the clone, because the test runs the real file,
    while the path it is deployed to and the marker it writes live in the
    temporary tree.
    """

    marker = tmp_path / "marker"
    monkeypatch.setattr(values, "PROGRAM_DEPLOY_PATH", tmp_path / "pyntara-btrfs-recompress")
    monkeypatch.setattr(values, "DONE_MARKER_PATH", marker)
    monkeypatch.setattr(values, "DONE_MARKER_PATH", marker)
    monkeypatch.setattr(values, "MINIMUM_FREE_GIB", minimum_free_gib)
    monkeypatch.setattr(values, "DEFRAGMENTED_MOUNT_POINTS", ("/",))
    monkeypatch.setattr(values, "JOB_UNIT_NAME", "test-pyntara-btrfs-recompress")
    return marker


def _commands_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    root_answer: str = BTRFS_ROOT_ANSWER,
    job_rc: int = 0,
    terminal: str | None = None,
) -> list[list[str]]:
    """Answer every command of the task; record the calls.

    findmnt answers with the given mount line, systemd-run answers with job_rc,
    every other command succeeds. shutil.which answers with the terminal the
    caller names, so a test decides whether the machine has a desktop window.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "findmnt":
            return _FakeProc(0, root_answer)
        if command[0] == "systemd-run" and job_rc != 0:
            if kwargs.get("check", False):
                raise subprocess.CalledProcessError(job_rc, command, "")
            return _FakeProc(job_rc, "")
        return _FakeProc(0, "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    monkeypatch.setattr(
        "pyntara.tasks.btrfs_recompress.shutil.which",
        lambda name: terminal,
    )
    return calls


def test_recompress_is_not_repeated_once_the_marker_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The work rewrites the whole filesystem, so the marker of a finished run
    # is what keeps a later run from doing it a second time.
    marker = _use_values(monkeypatch, tmp_path)
    marker.write_text("{}\n", encoding="utf-8")
    _commands_fake(monkeypatch)
    monkeypatch.setattr(
        btrfs_recompress, "_deploy_program", lambda ctx, warnings: pytest.fail("no deploy")
    )

    result = btrfs_recompress.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert "already done" in (result.message or "")


def test_recompress_runs_again_when_the_run_forces_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The force mode repeats the work, so the marker of the earlier run is
    # removed before the job starts; a marker left behind would make the next
    # run skip work the forced run may not have finished.
    marker = _use_values(monkeypatch, tmp_path)
    marker.write_text("{}\n", encoding="utf-8")
    calls = _commands_fake(monkeypatch, terminal=None)

    result = btrfs_recompress.task(_ctx(force=True))

    assert result.success is True
    assert result.changed is True
    assert marker.exists() is False
    assert any(command[0] == "systemd-run" for command in calls)


def test_recompress_skips_a_machine_that_does_not_run_on_btrfs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine without btrfs keeps working without the rewrite, and the task
    # reports it as a warning of a completed task.
    _use_values(monkeypatch, tmp_path)
    calls = _commands_fake(monkeypatch, root_answer=EXT4_ROOT_ANSWER)

    result = btrfs_recompress.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert result.warnings and "ext4" in result.warnings[0]
    assert calls == [calls[0]] and calls[0][0] == "findmnt"


def test_recompress_skips_a_machine_with_too_little_room(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A rewrite allocates new extents before it releases the old ones, so a
    # machine without room is reported instead of being filled up.
    _use_values(monkeypatch, tmp_path, minimum_free_gib=10**9)
    _commands_fake(monkeypatch)

    result = btrfs_recompress.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert result.warnings and "free" in result.warnings[0]


def test_recompress_deploys_the_program_and_starts_the_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The deployed program is the shipped file, it is executable, and the job
    # runs it with the option names of program_command.
    _use_values(monkeypatch, tmp_path)
    calls = _commands_fake(monkeypatch, terminal=None)

    result = btrfs_recompress.task(_ctx())

    assert result.success is True
    assert result.changed is True
    deployed = values.PROGRAM_DEPLOY_PATH
    assert deployed.read_text(encoding="utf-8") == PROGRAM_PATH.read_text(encoding="utf-8")
    assert deployed.stat().st_mode & 0o777 == values.PROGRAM_FILE_MODE

    job = next(command for command in calls if command[0] == "systemd-run")
    assert job[1] == f"--unit={values.JOB_UNIT_NAME}"
    assert "--collect" in job
    assert job[-len(btrfs_recompress.program_command()) :] == list(
        btrfs_recompress.program_command()
    )


def test_recompress_reports_a_program_it_cannot_deploy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The clone may be missing the file of the program; the task then reports
    # it and still asks for the job, which the target machine starts from the
    # program it already carries.
    _use_values(monkeypatch, tmp_path)
    _commands_fake(monkeypatch, terminal=None)
    monkeypatch.setattr(btrfs_recompress, "_deploy_program", _deploy_failure)

    result = btrfs_recompress.task(_ctx())

    assert result.success is True
    assert result.warnings and "cannot read" in result.warnings[0]


def _deploy_failure(ctx: Context, warnings: list[str]) -> bool:
    warnings.append("cannot read the program of the section")
    return False


def test_recompress_reports_a_job_that_cannot_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine whose job cannot start is reported, and the section still tells
    # the user which unit to look at.
    _use_values(monkeypatch, tmp_path)
    _commands_fake(monkeypatch, job_rc=1, terminal=None)

    result = btrfs_recompress.task(_ctx())

    assert result.success is True
    assert result.warnings and values.JOB_UNIT_NAME in result.warnings[0]


def test_recompress_opens_a_window_when_the_machine_has_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The work takes minutes and the user is not a developer, so the journal of
    # the job appears in a window on the desktop of that user.
    _use_values(monkeypatch, tmp_path)
    calls = _commands_fake(monkeypatch, terminal="/usr/bin/konsole")

    result = btrfs_recompress.task(_ctx())

    window = [
        command
        for command in calls
        if command[0] == "systemd-run" and f"--unit={values.WINDOW_UNIT_NAME}" in command
    ]
    assert result.changed is True
    assert window, "the window was not started"
    assert window[0][-5:] == [
        "-e",
        "journalctl",
        "-f",
        "-u",
        values.JOB_UNIT_NAME,
    ]


def test_recompress_reports_a_machine_without_a_desktop_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A server has no window to show, and the user is told where the messages
    # are instead of being left with a silent job.
    _use_values(monkeypatch, tmp_path)
    _commands_fake(monkeypatch, terminal=None)
    monkeypatch.setattr(values, "TERMINAL_COMMAND", "konsole-that-is-not-installed")

    result = btrfs_recompress.task(_ctx())

    assert result.success is True
    assert result.warnings
    assert values.JOB_UNIT_NAME in result.warnings[0]


def test_recompress_runs_the_program_with_the_options_it_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The option names are the interface between the task and the program, so
    # the real program runs with the command line the task builds and a
    # stand-in for btrfs records what it asked for.
    marker = _use_values(monkeypatch, tmp_path)
    btrfs_log = tmp_path / "btrfs.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stand_in = bin_dir / "btrfs"
    stand_in.write_text(
        "#!/bin/sh\n" f'printf "%s\\n" "$*" >> "{btrfs_log}"\n' "exit 0\n",
        encoding="utf-8",
    )
    stand_in.chmod(0o755)

    environment = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    completed = subprocess.run(
        [sys.executable, str(PROGRAM_PATH), *btrfs_recompress.program_command()],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    asked = btrfs_log.read_text(encoding="utf-8").splitlines()
    assert asked[0] == (
        f"filesystem defragment -r -c{values.COMPRESSION_ALGORITHM} "
        f"-L {values.COMPRESSION_LEVEL} -f /"
    )
    assert asked[-1] == (
        f"balance start -dusage={values.BALANCE_USAGE_PERCENT} --full-balance /"
    )
    assert "free space after the rewrite of /" in completed.stdout
    assert "free space after the balance" in completed.stdout
    assert marker.is_file()
    summary = json.loads(marker.read_text(encoding="utf-8"))
    assert summary["failures"] == []
    assert summary["free_before_mib"] > 0


def test_recompress_program_reports_a_failing_step_and_leaves_no_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The marker means the one-off work is done, so a failing step must not
    # leave one; the exit code carries the failure.
    marker = _use_values(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stand_in = bin_dir / "btrfs"
    stand_in.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stand_in.chmod(0o755)

    environment = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    completed = subprocess.run(
        [sys.executable, str(PROGRAM_PATH), *btrfs_recompress.program_command()],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 1
    assert marker.exists() is False
    assert "did not finish" in completed.stdout


def test_recompress_program_refuses_a_machine_without_room(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The program checks the room itself, so a machine whose space ran out
    # between the decision and the job is not filled up.
    marker = _use_values(monkeypatch, tmp_path)
    command = [
        sys.executable,
        str(PROGRAM_PATH),
        *btrfs_recompress.program_command(),
        "--minimum-free-gib",
        "1000000000",
    ]

    completed = subprocess.run(command, capture_output=True, text=True, check=False)

    assert completed.returncode == 2
    assert marker.exists() is False
    assert "no room" in completed.stdout


def test_recompress_program_leaves_out_a_mount_point_that_is_not_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine may not carry the second mount point at all, and the program
    # says so instead of failing the whole run.
    _use_values(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stand_in = bin_dir / "btrfs"
    stand_in.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stand_in.chmod(0o755)
    marker = tmp_path / "marker"
    environment = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    command = [
        sys.executable,
        str(PROGRAM_PATH),
        *btrfs_recompress.program_command(),
        "--defragment-path",
        str(tmp_path / "not-mounted"),
        "--marker-file",
        str(marker),
    ]

    completed = subprocess.run(
        command, capture_output=True, text=True, env=environment, check=False
    )

    assert completed.returncode == 0
    assert "is not present on this machine" in completed.stdout


def test_recompress_depends_on_the_storage_setup() -> None:
    # The rewrite needs the tools and the compression options of the storage
    # setup section, so the catalog orders it after that section.
    records = {record.name: record for record in tasks_values.CATALOG}

    assert "btrfs_setup" in records["btrfs_recompress"].depends
    assert "btrfs_recompress" in {record.name for record in tasks_values.CATALOG}


def test_recompress_belongs_to_every_install_mode() -> None:
    # Every machine gets compressed storage, including the quick set.
    records = {record.name: record for record in tasks_values.CATALOG}

    assert records["btrfs_recompress"].modes == tasks_values.MODES


def test_the_recompression_writes_the_compression_the_storage_setup_declares() -> None:
    # One machine has one compression: the option the fstab lines receive and
    # the option the one-off rewrite writes are the same fact, so the two
    # sections read it from one place. A drift here would rewrite the machine
    # with another compression than the one it mounts with.
    option = setup_values.COMPRESSION_OPTION_ASSIGNMENT

    assert option.startswith("compress=")
    algorithm, _, level = option.split("=", 1)[1].partition(":")
    assert values.COMPRESSION_ALGORITHM == algorithm
    assert values.COMPRESSION_LEVEL == int(level)
    assert values.DEFRAGMENTED_MOUNT_POINTS == setup_values.COMPRESSED_MOUNT_POINTS


def test_the_setup_values_are_read_by_the_recompress_section() -> None:
    # The sections share the storage facts through the values of the setup
    # section, so a rename there is caught here instead of on a target machine.
    assert setup_values.ROOT_MOUNT_POINT.is_absolute()
    assert setup_values.BTRFS_FILESYSTEM_TYPE == "btrfs"
