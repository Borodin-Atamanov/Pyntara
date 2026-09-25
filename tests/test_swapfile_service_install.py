"""Unit tests for the swapfile_service_install task.

The task deploys a program and a unit file and runs the program. Every external
resource is pointed at the temporary tree through the values modules (the
swapfile path, the deployed program path, the systemd unit directory) and
subprocess.run is replaced by a recording fake, so no test writes outside its
temporary directory and none touches the real /swapfile. One test runs the real
program with the command line the task builds, which is what keeps the option
names of the two sides equal.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from support import REPO_ROOT, make_context
from support import FakeProc as _FakeProc

from pyntara.context import Context
from pyntara.tasks import swapfile_service_install
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import swapfile_service_install as values

UNIT_TEMPLATE = """\
[Unit]
Description=Create and activate the swap file
After=local-fs.target
RequiresMountsFor=$swapfile_path

[Service]
Type=oneshot
RemainAfterExit=yes
$exec_lines
ExecStop=$swapoff_path $swapfile_path

[Install]
WantedBy=multi-user.target
"""

PROGRAM_TEXT = "#!/usr/bin/python3\nprint('program')\n"

RESULT_LINE = json.dumps({"changed": True, "skipped_reason": None, "error": None})
UNCHANGED_LINE = json.dumps({"changed": False, "skipped_reason": None, "error": None})


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own swapfile, program and unit directory.

    The paths and the unit directory are values, so the tests never touch
    /swapfile, /usr/local/bin or /etc/systemd/system.
    """

    monkeypatch.setattr(values, "SWAPFILE_PATH", tmp_path / "swapfile")
    monkeypatch.setattr(
        values, "PROGRAM_DEPLOY_PATH", tmp_path / "bin" / "pyntara-swapfile"
    )
    monkeypatch.setattr(common_values, "MEMINFO_TOTAL_KEY", "MemTotal:")
    monkeypatch.setattr(engine_values, "SYSTEMD_UNIT_DIR", tmp_path / "systemd")


# The path the tests answer the run-time lookup of swapoff with.
SWAPOFF_PATH = "/usr/sbin/swapoff"


@pytest.fixture(autouse=True)
def _without_apt_and_with_a_known_swapoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the package helper and the tool lookup away from the machine.

    The task installs its packages through the shared helper and discovers the
    path of swapoff at run time; both would reach the real machine, so the tests
    answer with a machine whose packages are already installed and whose swapoff
    sits at a fixed path. The tests that care about either behaviour replace
    these answers again.
    """

    monkeypatch.setattr(
        swapfile_service_install,
        "install_missing_packages",
        lambda context, packages: ([], [], [], []),
    )
    monkeypatch.setattr(
        swapfile_service_install,
        "_command_path",
        lambda command_name: SWAPOFF_PATH,
    )


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    """Context of the task with its data files in the temporary tree."""

    return make_context(
        task_name="swapfile_service_install",
        install_mode="server",
        force_tasks=(frozenset({"swapfile_service_install"}) if force else frozenset()),
        task_data_root=tmp_path,
        repo_root=tmp_path,
        skip_apt_update=True,
    )


def _write_data_files(tmp_path: Path, *, program_text: str = PROGRAM_TEXT) -> Path:
    """Write the program and the unit template where the task reads them."""

    data_dir = tmp_path / "task_data" / "swapfile_service_install"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / values.PROGRAM_FILE_NAME).write_text(program_text, encoding="utf-8")
    (data_dir / values.UNIT_TEMPLATE_FILE_NAME).write_text(
        UNIT_TEMPLATE, encoding="utf-8"
    )
    return data_dir


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = False,
    program_result: _FakeProc | None = None,
) -> list[list[str]]:
    """Replace subprocess.run; return the recorded command lines.

    systemctl is-enabled answers from the enabled flag and the program of the
    section answers with the given process result; every other command succeeds.
    """

    calls: list[list[str]] = []
    program_path = str(values.PROGRAM_DEPLOY_PATH)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "systemctl" and command[1] == "is-enabled":
            if enabled:
                return _FakeProc(0, "enabled\n")
            return _FakeProc(1, "disabled")
        if command[0] == program_path:
            if program_result is not None:
                return program_result
            return _FakeProc(0, RESULT_LINE + "\n")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _program_call(calls: list[list[str]]) -> list[str]:
    """The recorded command line of the deployed program."""

    program_path = str(values.PROGRAM_DEPLOY_PATH)
    for call in calls:
        if call[0] == program_path:
            return call
    raise AssertionError("the program was never called")


def _unit_text(tmp_path: Path) -> str:
    """The unit file the task wrote into the temporary unit directory."""

    return (tmp_path / "systemd" / values.SERVICE_UNIT_NAME).read_text(encoding="utf-8")


def test_a_missing_value_is_reported_and_nothing_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    monkeypatch.setattr(
        values, "READ_VALUE_NAMES", values.READ_VALUE_NAMES + ("NOT_DECLARED",)
    )
    calls = _install_fake(monkeypatch)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert "NOT_DECLARED" in result.warnings[0]
    assert calls == []


def test_the_program_is_deployed_with_the_declared_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    _install_fake(monkeypatch)
    result = swapfile_service_install.task(_ctx(tmp_path))
    deployed = values.PROGRAM_DEPLOY_PATH
    assert deployed.read_text(encoding="utf-8") == PROGRAM_TEXT
    assert deployed.stat().st_mode & 0o777 == values.PROGRAM_FILE_MODE
    assert result.success is True


def test_a_program_source_that_cannot_be_read_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The program file is missing from the clone data directory: the task
    # reports it and installs no service that could not start.
    data_dir = _write_data_files(tmp_path)
    (data_dir / values.PROGRAM_FILE_NAME).unlink()
    calls = _install_fake(monkeypatch)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.changed is False
    assert "cannot read the program" in result.warnings[0]
    assert not (tmp_path / "systemd" / values.SERVICE_UNIT_NAME).exists()
    assert calls == []


def test_the_unit_carries_the_command_line_of_the_program(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    calls = _install_fake(monkeypatch)
    swapfile_service_install.task(_ctx(tmp_path))
    command = _program_call(calls)
    unit = _unit_text(tmp_path)
    assert f"ExecStart={' '.join(command)}" in unit
    assert f"ExecStop={SWAPOFF_PATH} {values.SWAPFILE_PATH}" in unit
    assert f"RequiresMountsFor={values.SWAPFILE_PATH}" in unit


def test_the_service_is_enabled_when_it_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    calls = _install_fake(monkeypatch, enabled=False)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", values.SERVICE_UNIT_NAME] in calls
    assert result.changed is True


def test_a_configured_machine_reports_no_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The program is deployed byte for byte, the unit is current, the service
    # is enabled and the program reports the target state as reached: the task
    # changes nothing.
    _write_data_files(tmp_path)
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    command = swapfile_service_install._program_command(force=False)
    (unit_dir / values.SERVICE_UNIT_NAME).write_text(
        swapfile_service_install._render_unit(
            tmp_path
            / "task_data"
            / "swapfile_service_install"
            / values.UNIT_TEMPLATE_FILE_NAME,
            command,
            values.SWAPFILE_PATH,
            SWAPOFF_PATH,
        ),
        encoding="utf-8",
    )
    values.PROGRAM_DEPLOY_PATH.parent.mkdir(parents=True)
    values.PROGRAM_DEPLOY_PATH.write_text(PROGRAM_TEXT, encoding="utf-8")
    calls = _install_fake(
        monkeypatch, enabled=True, program_result=_FakeProc(0, UNCHANGED_LINE + "\n")
    )
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.changed is False
    assert result.message == "already configured"
    assert ["systemctl", "daemon-reload"] not in calls
    assert ["systemctl", "enable", values.SERVICE_UNIT_NAME] not in calls


def test_the_result_of_the_program_drives_the_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    _install_fake(
        monkeypatch, enabled=True, program_result=_FakeProc(0, RESULT_LINE + "\n")
    )
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.changed is True


def test_a_refused_storage_becomes_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The program refuses storage that keeps its data in memory and answers
    # with the reason; the task keeps the service and reports the reason.
    _write_data_files(tmp_path)
    line = json.dumps(
        {
            "changed": False,
            "skipped_reason": "the kernel refuses to activate swap on this storage",
            "error": None,
        }
    )
    _install_fake(monkeypatch, enabled=False, program_result=_FakeProc(0, line + "\n"))
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True  # the deployment and the service still changed
    assert "no swap file was created" in result.warnings[0]
    assert "the kernel refuses" in result.warnings[0]
    assert (tmp_path / "systemd" / values.SERVICE_UNIT_NAME).exists()


def test_a_failed_program_becomes_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    line = json.dumps(
        {"changed": False, "skipped_reason": None, "error": "mkswap failed: no space"}
    )
    _install_fake(monkeypatch, enabled=True, program_result=_FakeProc(1, line + "\n"))
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert "the swap program failed" in result.warnings[0]
    assert "no space" in result.warnings[0]


def test_an_unreadable_result_becomes_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    _install_fake(monkeypatch, enabled=True, program_result=_FakeProc(0, "some text\n"))
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert "reported nothing readable" in result.warnings[0]


def test_the_shared_package_helper_installs_the_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    recorded: list[object] = []

    def fake_install(
        context: object, packages: object
    ) -> tuple[list[str], list[str], list[tuple[str, str]], list[str]]:
        recorded.append(packages)
        return ([], [], [], [])

    monkeypatch.setattr(
        swapfile_service_install, "install_missing_packages", fake_install
    )
    _install_fake(monkeypatch)
    swapfile_service_install.task(_ctx(tmp_path))
    assert recorded == [values.PACKAGES]


def test_a_package_that_cannot_be_installed_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    monkeypatch.setattr(
        swapfile_service_install,
        "install_missing_packages",
        lambda context, packages: (
            list(packages),
            [],
            [("e2fsprogs", "not found")],
            [],
        ),
    )
    _install_fake(monkeypatch)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert any("e2fsprogs: not found" in warning for warning in result.warnings)


def test_a_machine_without_swapoff_is_reported_and_nothing_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    monkeypatch.setattr(
        swapfile_service_install, "_command_path", lambda command_name: None
    )
    calls = _install_fake(monkeypatch)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.changed is False
    assert any("swapoff" in warning for warning in result.warnings)
    assert not (tmp_path / "systemd" / values.SERVICE_UNIT_NAME).exists()
    assert calls == []


def test_the_unit_is_started_as_the_artifact_of_the_next_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    calls = _install_fake(monkeypatch, enabled=True)
    swapfile_service_install.task(_ctx(tmp_path))
    assert ["systemctl", "start", values.SERVICE_UNIT_NAME] in calls


def test_force_passes_the_force_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    calls = _install_fake(monkeypatch, enabled=True)
    swapfile_service_install.task(_ctx(tmp_path, force=True))
    assert "--force" in _program_call(calls)
    assert "--force" in _unit_text(tmp_path)


def test_the_program_reads_the_command_line_the_task_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real program, started with the options this task builds.

    This is the test that keeps the two sides together: the option names of the
    program and of the task belong to one interface, and nothing else in the
    repository would notice a name that drifts on one side.
    """

    program_path = (
        REPO_ROOT / "task_data" / "swapfile_service_install" / values.PROGRAM_FILE_NAME
    )
    spec = importlib.util.spec_from_file_location(
        "configure_swapfile_contract", program_path
    )
    assert spec is not None and spec.loader is not None
    program = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = program
    spec.loader.exec_module(program)

    recorded: list[list[str]] = []

    def fake_run(
        command: list[str], timeout_seconds: float
    ) -> subprocess.CompletedProcess[str]:
        recorded.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(program, "_run", fake_run)
    monkeypatch.setattr(program, "_tool_path", lambda tool_name: tool_name)
    command = swapfile_service_install._program_command(force=False)
    exit_code = program.main(list(command[1:]))
    assert exit_code == 0
    assert recorded[0] == ["swapon", "--show", "--noheadings"]
    assert any(
        part.startswith("--swapfile") or part == str(values.SWAPFILE_PATH)
        for part in command
    )
