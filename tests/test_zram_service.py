"""Unit tests for the zram_service task.

The task deploys a program and a unit file and runs the program. Every external
resource is pointed at the temporary tree through the values modules (the
deployed program path, the systemd unit directory, the kernel files the program
reads) and subprocess.run is replaced by a recording fake, so no test writes
outside its temporary directory and none touches the real zram devices. One test
runs the real program with the command line the task builds, which is what keeps
the option names of the two sides equal.
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
from pyntara.tasks import zram_service
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import zram_service as values

UNIT_TEMPLATE = """\
[Unit]
Description=Configure ZRAM swap devices
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
$exec_lines

[Install]
WantedBy=multi-user.target
"""

PROGRAM_TEXT = "#!/usr/bin/python3\nprint('program')\n"

RESULT_LINE = json.dumps(
    {
        "changed": True,
        "skipped_reason": None,
        "error": None,
        "warnings": [],
    }
)
UNCHANGED_LINE = json.dumps(
    {
        "changed": False,
        "skipped_reason": None,
        "error": None,
        "warnings": [],
    }
)


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own program, unit directory and kernel files.

    The paths and the unit directory are values, so the tests never touch
    /usr/local/bin or /etc/systemd/system. The kernel files the program reads are
    pointed at the temporary tree as well, so no test reads the memory or the
    core count of the machine that runs the tests.
    """

    monkeypatch.setattr(
        values, "PROGRAM_DEPLOY_PATH", tmp_path / "bin" / "pyntara-zram"
    )
    monkeypatch.setattr(engine_values, "SYSTEMD_UNIT_DIR", tmp_path / "systemd")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16777216 kB\n", encoding="utf-8")
    monkeypatch.setattr(common_values, "MEMINFO_PATH", meminfo)
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor : 0\nprocessor : 1\n", encoding="utf-8")
    monkeypatch.setattr(values, "CPUINFO_PATH", cpuinfo)


@pytest.fixture(autouse=True)
def _without_apt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the package helper away from the machine.

    The task installs its packages through the shared helper, which would reach
    the real machine; the tests answer with a machine whose packages are already
    installed. The test that cares about the helper replaces this answer again.
    """

    monkeypatch.setattr(
        zram_service,
        "install_missing_packages",
        lambda context, packages: ([], [], [], []),
    )


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    """Context of the task with its data files in the temporary tree."""

    return make_context(
        task_name="zram_service",
        install_mode="server",
        force_tasks=(frozenset({"zram_service"}) if force else frozenset()),
        task_data_root=tmp_path,
        repo_root=tmp_path,
        skip_apt_update=True,
    )


def _write_data_files(tmp_path: Path, *, program_text: str = PROGRAM_TEXT) -> Path:
    """Write the program and the unit template where the task reads them."""

    data_dir = tmp_path / "task_data" / "zram_service"
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
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert "NOT_DECLARED" in result.warnings[0]
    assert calls == []


def test_the_program_is_deployed_with_the_declared_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    _install_fake(monkeypatch)
    result = zram_service.task(_ctx(tmp_path))
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
    result = zram_service.task(_ctx(tmp_path))
    assert result.changed is False
    assert "cannot read the program" in result.warnings[0]
    assert not (tmp_path / "systemd" / values.SERVICE_UNIT_NAME).exists()
    assert not any(call[0] == "systemctl" for call in calls)


def test_the_unit_carries_the_command_line_of_the_program(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    calls = _install_fake(monkeypatch)
    zram_service.task(_ctx(tmp_path))
    command = _program_call(calls)
    assert f"ExecStart={' '.join(command)}" in _unit_text(tmp_path)
    assert command[0] == str(values.PROGRAM_DEPLOY_PATH)


def test_the_service_is_enabled_when_it_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    calls = _install_fake(monkeypatch, enabled=False)
    result = zram_service.task(_ctx(tmp_path))
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", values.SERVICE_UNIT_NAME] in calls
    assert result.changed is True


def test_a_configured_machine_reports_no_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The program is deployed byte for byte, the unit is current, the service is
    # enabled and the program reports the target state as reached: the task
    # changes nothing.
    _write_data_files(tmp_path)
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    command = zram_service._program_command(force=False)
    (unit_dir / values.SERVICE_UNIT_NAME).write_text(
        zram_service._render_unit(
            tmp_path / "task_data" / "zram_service" / values.UNIT_TEMPLATE_FILE_NAME,
            command,
        ),
        encoding="utf-8",
    )
    values.PROGRAM_DEPLOY_PATH.parent.mkdir(parents=True)
    values.PROGRAM_DEPLOY_PATH.write_text(PROGRAM_TEXT, encoding="utf-8")
    calls = _install_fake(
        monkeypatch, enabled=True, program_result=_FakeProc(0, UNCHANGED_LINE + "\n")
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.changed is False
    assert (result.message or "").startswith("already configured")
    assert ["systemctl", "daemon-reload"] not in calls
    assert ["systemctl", "enable", values.SERVICE_UNIT_NAME] not in calls


def test_the_result_of_the_program_drives_the_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    _install_fake(
        monkeypatch, enabled=True, program_result=_FakeProc(0, RESULT_LINE + "\n")
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.changed is True


def test_a_failed_program_becomes_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    line = json.dumps(
        {
            "changed": False,
            "skipped_reason": None,
            "error": "cannot read /proc/meminfo",
            "warnings": [],
        }
    )
    _install_fake(monkeypatch, enabled=True, program_result=_FakeProc(1, line + "\n"))
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert any("the zram program failed" in warning for warning in result.warnings)


def test_a_skipped_reason_becomes_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    line = json.dumps(
        {
            "changed": False,
            "skipped_reason": "the storage cannot hold swap",
            "error": None,
            "warnings": [],
        }
    )
    _install_fake(monkeypatch, enabled=True, program_result=_FakeProc(0, line + "\n"))
    result = zram_service.task(_ctx(tmp_path))
    assert any(
        "no zram device was configured" in warning for warning in result.warnings
    )
    assert any("the storage cannot hold swap" in warning for warning in result.warnings)


def test_the_program_warnings_become_warnings_of_the_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    line = json.dumps(
        {
            "changed": True,
            "skipped_reason": None,
            "error": None,
            "warnings": ["cannot configure zram1: permission denied"],
        }
    )
    _install_fake(monkeypatch, enabled=True, program_result=_FakeProc(0, line + "\n"))
    result = zram_service.task(_ctx(tmp_path))
    assert any("cannot configure zram1" in warning for warning in result.warnings)


def test_an_unreadable_result_becomes_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    _install_fake(monkeypatch, enabled=True, program_result=_FakeProc(0, "no json\n"))
    result = zram_service.task(_ctx(tmp_path))
    assert any("reported nothing readable" in warning for warning in result.warnings)


def test_a_missing_template_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit template is missing: the program is deployed, the service file is
    # skipped alone and the task completes.
    data_dir = _write_data_files(tmp_path)
    (data_dir / values.UNIT_TEMPLATE_FILE_NAME).unlink()
    calls = _install_fake(monkeypatch, enabled=True)
    result = zram_service.task(_ctx(tmp_path))
    assert result.changed is True
    assert any("template" in warning for warning in result.warnings)
    assert not any(call[:2] == ["systemctl", "enable"] for call in calls)


def test_the_unit_is_started_as_the_artifact_of_the_next_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    calls = _install_fake(monkeypatch, enabled=True)
    zram_service.task(_ctx(tmp_path))
    assert ["systemctl", "start", values.SERVICE_UNIT_NAME] in calls


def test_force_passes_the_force_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    calls = _install_fake(monkeypatch, enabled=True)
    zram_service.task(_ctx(tmp_path, force=True))
    assert "--force" in _program_call(calls)


def test_no_argument_of_the_command_line_carries_a_space() -> None:
    # The unit renders the command line as words separated by spaces, so an
    # argument with a space inside arrives at the program as two arguments and
    # the boot service fails to start. This guards the interface of the unit.
    command = zram_service._program_command(force=False)
    assert not [argument for argument in command if " " in argument]


def test_the_shared_package_helper_installs_the_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_data_files(tmp_path)
    _install_fake(monkeypatch)
    recorded: list[tuple[str, ...]] = []

    def fake_install(
        context: Context, packages: tuple[str, ...]
    ) -> tuple[list[str], list[str], list[tuple[str, str]], list[str]]:
        recorded.append(tuple(packages))
        return [], [], [], []

    monkeypatch.setattr(zram_service, "install_missing_packages", fake_install)
    zram_service.task(_ctx(tmp_path))
    assert recorded == [values.PACKAGES]


def test_the_program_reads_the_command_line_the_task_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real program, started with the options this task builds.

    This is the test that keeps the two sides together: the option names of the
    program and of the task belong to one interface, and nothing else in the
    repository would notice a name that drifts on one side.
    """

    program_path = REPO_ROOT / "task_data" / "zram_service" / values.PROGRAM_FILE_NAME
    spec = importlib.util.spec_from_file_location(
        "configure_zram_contract", program_path
    )
    assert spec is not None and spec.loader is not None
    program = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = program
    spec.loader.exec_module(program)

    sys_block = tmp_path / "sys" / "block"
    sys_block.mkdir(parents=True)
    control = tmp_path / "sys" / "class" / "zram-control"
    control.mkdir(parents=True)
    (control / "hot_remove").write_text("", encoding="utf-8")
    (control / "hot_add").write_text("", encoding="utf-8")
    monkeypatch.setattr(program, "SYS_BLOCK_PATH", sys_block)
    monkeypatch.setattr(program, "HOT_ADD_PATH", control / "hot_add")
    monkeypatch.setattr(program, "HOT_REMOVE_PATH", control / "hot_remove")

    recorded: list[list[str]] = []

    def fake_run(
        command: list[str], timeout_seconds: float
    ) -> subprocess.CompletedProcess[str]:
        recorded.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(program, "_run", fake_run)
    monkeypatch.setattr(program, "_tool_path", lambda tool_name: tool_name)
    command = zram_service._program_command(force=False)
    exit_code = program.main(list(command[1:]))
    assert exit_code == 0
    assert recorded[0] == ["swapon", "--show", "--noheadings"]
