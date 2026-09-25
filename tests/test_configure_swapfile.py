"""Unit tests for the swap setup program of the swapfile_service_install task.

The program lives under task_data/ and is not part of the package, so it is
loaded through importlib.util (the same way the other deployed programs are
loaded in the tests). Every command the program runs is replaced by a recording
double, so no test formats, activates or removes anything on this machine. The
command line is checked through a real process with stub commands on PATH, which
covers the argument plumbing end to end.

The live mechanism was proved by hand on the machine and is not repeated here:
the kernel refuses swap on a tmpfs file with Invalid argument, and a small file
on the root filesystem is created, formatted and activated.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from support import REPO_ROOT

_PROGRAM_PATH = (
    REPO_ROOT / "task_data" / "swapfile_service_install" / "configure_swapfile.py"
)


def _load_program() -> types.ModuleType:
    """Load the deployed program as a module for the in-process tests.

    The module is registered under its name before it is executed, because the
    dataclasses of the program ask sys.modules for their own module while the
    class is built.
    """

    spec = importlib.util.spec_from_file_location(
        "configure_swapfile_under_test", _PROGRAM_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


program: types.ModuleType = _load_program()

# The real lookup, which the fixture below replaces for the tests that read a
# recorded command; the test of a missing tool calls this one directly.
_REAL_TOOL_PATH = program._tool_path


@pytest.fixture(autouse=True)
def _tools_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recorded commands name the tools directly.

    The program discovers the absolute path of every tool at run time, which
    depends on the machine that runs the tests; the tests replace that lookup
    with the name itself, so a recorded command reads the way it would with the
    tools at their usual place.
    """

    monkeypatch.setattr(program, "_tool_path", lambda tool_name: tool_name)


def test_a_tool_the_machine_does_not_carry_is_named() -> None:
    with pytest.raises(program.SwapfileError) as raised:
        _REAL_TOOL_PATH("pyntara-no-such-tool")
    assert "pyntara-no-such-tool" in str(raised.value)

MEMINFO_TEXT = "MemTotal:       16777216 kB\n"
RAM_KIB = 16 * 1024 * 1024


def _answered(
    command: list[str], returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    """One canned answer of the command double."""

    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _run_double(
    calls: list[list[str]],
    handler: Callable[[list[str]], subprocess.CompletedProcess[str] | None]
    | None = None,
) -> Callable[[list[str], float], subprocess.CompletedProcess[str]]:
    """A replacement for the run helper that records and never executes.

    Every command is appended to calls; the handler answers the commands a test
    cares about, and everything else succeeds with no output, which is the state
    of a machine whose commands work.
    """

    def run(
        command: list[str], timeout_seconds: float
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        if handler is not None:
            answer = handler(command)
            if answer is not None:
                return answer
        return _answered(command, 0)

    return run


def _config(tmp_path: Path, **overrides: Any) -> Any:
    """A configuration with a one MiB target on the temporary filesystem.

    The RAM term is one mebibyte and the disk term is far larger, so the target
    of a test never depends on the free space of the machine that runs it.
    """

    settings: dict[str, Any] = {
        "swapfile_path": tmp_path / "swapfile",
        "file_mode": 0o600,
        "ram_multiplier": 0.0,
        "ram_extra_mb": 1,
        "disk_fraction": 0.5,
        "size_tolerance_mb": 1,
        "probe_size_kb": 512,
        "meminfo_path": tmp_path / "meminfo",
        "meminfo_total_key": "MemTotal:",
        "command_timeout_seconds": 60.0,
        "force": False,
    }
    settings.update(overrides)
    (tmp_path / "meminfo").write_text(MEMINFO_TEXT, encoding="utf-8")
    return program.Config(**settings)


def _arguments(config: Any, *, force: bool = False) -> list[str]:
    """The command line of one configuration, as the task builds it."""

    arguments = [
        "--swapfile",
        str(config.swapfile_path),
        "--file-mode",
        f"{config.file_mode:o}",
        "--ram-multiplier",
        str(config.ram_multiplier),
        "--ram-extra-mb",
        str(config.ram_extra_mb),
        "--disk-fraction",
        str(config.disk_fraction),
        "--size-tolerance-mb",
        str(config.size_tolerance_mb),
        "--probe-size-kb",
        str(config.probe_size_kb),
        "--meminfo",
        str(config.meminfo_path),
        "--meminfo-total-key",
        config.meminfo_total_key,
        "--command-timeout-seconds",
        str(config.command_timeout_seconds),
    ]
    if force:
        arguments.append("--force")
    return arguments


def test_the_target_is_the_smaller_of_the_ram_term_and_the_disk_term(
    tmp_path: Path,
) -> None:
    # The free space arrives in kibibytes like the memory does: 16384 MiB of RAM
    # * 1.6 + 4096 MiB is 30310 MiB, a disk of 100000 MiB free allows 50000 MiB,
    # so the RAM term wins, and one of 40000 MiB free allows 20000 MiB, so the
    # disk term wins.
    config = _config(tmp_path, ram_multiplier=1.6, ram_extra_mb=4096)
    free_for_ram_term = 100000 * 1024
    free_for_disk_term = 40000 * 1024
    assert (
        program._calculate_target_size_mb(RAM_KIB, free_for_ram_term, config) == 30310
    )
    assert (
        program._calculate_target_size_mb(RAM_KIB, free_for_disk_term, config) == 20000
    )


def test_an_active_swapfile_at_the_target_size_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config.swapfile_path.write_bytes(b"\0" * (1024 * 1024))
    calls: list[list[str]] = []

    def handler(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command[:2] == ["swapon", "--show"]:
            return _answered(command, 0, f"{config.swapfile_path} file 1M 0B -1\n")
        return None

    monkeypatch.setattr(program, "_run", _run_double(calls, handler))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is False
    assert outcome.skipped_reason is None
    assert [command[0] for command in calls] == ["swapon"]


def test_a_swapfile_at_the_target_size_is_activated_when_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config.swapfile_path.write_bytes(b"\0" * (1024 * 1024))
    calls: list[list[str]] = []
    monkeypatch.setattr(program, "_run", _run_double(calls, None))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is True
    assert ["swapon", str(config.swapfile_path)] in calls
    assert not any(command[0] == "fallocate" for command in calls)


def test_an_active_swap_that_is_only_a_prefix_is_not_the_same_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first column alone decides: a listing that carries a longer path
    # starting with the configured one must not pass for it.
    config = _config(tmp_path)
    config.swapfile_path.write_bytes(b"\0" * (1024 * 1024))
    calls: list[list[str]] = []

    def handler(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command[:2] == ["swapon", "--show"]:
            return _answered(
                command, 0, f"{config.swapfile_path}-other file 1M 0B -1\n"
            )
        return None

    monkeypatch.setattr(program, "_run", _run_double(calls, handler))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is True
    assert ["swapon", str(config.swapfile_path)] in calls


def test_a_probe_the_kernel_refuses_stops_the_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    calls: list[list[str]] = []
    probe_path = config.swapfile_path.with_name(config.swapfile_path.name + ".probe")

    def handler(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command[:2] == ["swapon", "--show"]:
            return _answered(command, 0, "")
        if command[0] == "swapon" and str(probe_path) in command:
            return _answered(
                command,
                1,
                "",
                f"swapon: {probe_path}: swapon failed: Invalid argument",
            )
        return None

    monkeypatch.setattr(program, "_run", _run_double(calls, handler))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is False
    assert outcome.skipped_reason is not None
    assert "Invalid argument" in outcome.skipped_reason
    assert not config.swapfile_path.exists()
    assert not any(command[0] == "fallocate" and "M" in command[2] for command in calls)
    assert not probe_path.exists()


def test_the_creation_sequence_runs_on_accepted_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(program, "_run", _run_double(calls, None))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is True
    probe_path = config.swapfile_path.with_name(config.swapfile_path.name + ".probe")
    assert calls == [
        ["swapon", "--show", "--noheadings"],
        ["chattr", "+C", str(probe_path)],
        ["fallocate", "-l", "512K", str(probe_path)],
        ["chmod", "0600", str(probe_path)],
        ["mkswap", str(probe_path)],
        ["swapon", str(probe_path)],
        ["swapoff", str(probe_path)],
        ["chattr", "+C", str(config.swapfile_path)],
        ["fallocate", "-l", "1M", str(config.swapfile_path)],
        ["chmod", "0600", str(config.swapfile_path)],
        ["mkswap", str(config.swapfile_path)],
        ["swapon", str(config.swapfile_path)],
    ]


def test_a_refused_no_cow_attribute_does_not_stop_the_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A filesystem without copy-on-write answers the attribute request with
    # "Operation not supported"; the file is still created, formatted and
    # activated, because only a copying filesystem needs the attribute.
    config = _config(tmp_path)
    calls: list[list[str]] = []

    def handler(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command[0] == "chattr":
            return _answered(command, 1, "", "chattr: Operation not supported")
        return None

    monkeypatch.setattr(program, "_run", _run_double(calls, handler))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is True
    assert ["mkswap", str(config.swapfile_path)] in calls
    assert ["swapon", str(config.swapfile_path)] in calls


def test_the_swap_directory_is_created_before_the_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The directory is what the free space is read from and where the probe and
    # the file are created, so it exists before any of that.
    directory = tmp_path / "swap"
    config = _config(tmp_path, swapfile_path=directory / "swapfile")
    monkeypatch.setattr(program, "_run", _run_double([], None))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is True
    assert directory.is_dir()


def test_force_creates_the_file_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, force=True)
    config.swapfile_path.write_bytes(b"\0" * (1024 * 1024))
    calls: list[list[str]] = []

    def handler(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command[:2] == ["swapon", "--show"]:
            return _answered(command, 0, f"{config.swapfile_path} file 1M 0B -1\n")
        return None

    monkeypatch.setattr(program, "_run", _run_double(calls, handler))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is True
    assert ["swapoff", str(config.swapfile_path)] in calls
    assert ["mkswap", str(config.swapfile_path)] in calls


def test_an_established_swap_is_deactivated_before_the_file_is_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A file of another size while the swap is active: the swap must come off
    # before the file is removed and created again.
    config = _config(tmp_path)
    config.swapfile_path.write_bytes(b"\0" * (4 * 1024 * 1024))
    calls: list[list[str]] = []

    def handler(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command[:2] == ["swapon", "--show"]:
            return _answered(command, 0, f"{config.swapfile_path} file 4M 0B -1\n")
        return None

    monkeypatch.setattr(program, "_run", _run_double(calls, handler))
    outcome = program.configure_swapfile(config)
    assert outcome.changed is True
    path = str(config.swapfile_path)
    commands = [[str(part) for part in command] for command in calls]
    assert commands.index(["swapoff", path]) < commands.index(["mkswap", path])
    assert commands.index(["swapoff", path]) < commands.index(["swapon", path])


def test_a_failed_step_is_reported_in_the_result_line_and_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    real_create = ["fallocate", "-l", "1M", str(config.swapfile_path)]

    def handler(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command == real_create:
            return _answered(command, 1, "", "fallocate: cannot allocate the file")
        return None

    monkeypatch.setattr(program, "_run", _run_double([], handler))
    exit_code = program.main(_arguments(config))
    assert exit_code == 1
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["changed"] is False
    assert "cannot allocate the file" in result["error"]


def test_an_unchanged_run_prints_its_result_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    config.swapfile_path.write_bytes(b"\0" * (1024 * 1024))

    def handler(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command[:2] == ["swapon", "--show"]:
            return _answered(command, 0, f"{config.swapfile_path} file 1M 0B -1\n")
        return None

    monkeypatch.setattr(program, "_run", _run_double([], handler))
    exit_code = program.main(_arguments(config))
    assert exit_code == 0
    printed = capsys.readouterr().out
    result = json.loads(printed.strip().splitlines()[-1])
    assert result == {"changed": False, "skipped_reason": None, "error": None}
    assert "target state already reached" in printed


def test_a_missing_memory_line_stops_the_program(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (tmp_path / "meminfo").write_text("SomethingElse: 1 kB\n", encoding="utf-8")
    with pytest.raises(program.SwapfileError):
        program.configure_swapfile(config)


def test_a_file_mode_that_is_not_octal_is_refused() -> None:
    result = subprocess.run(
        [sys.executable, str(_PROGRAM_PATH), "--file-mode", "not-a-mode"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "octal file mode" in result.stderr


def test_the_command_line_runs_the_creation_path_with_stub_commands(
    tmp_path: Path,
) -> None:
    """The real process, the real argument plumbing, stub commands on PATH."""

    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    call_log = tmp_path / "calls.log"
    stub = '#!/bin/sh\nprintf "%s\\n" "$(basename "$0") $*" >> "$CALL_LOG"\nexit 0\n'
    for name in ("chattr", "chmod", "fallocate", "mkswap", "swapon", "swapoff"):
        path = stub_directory / name
        path.write_text(stub, encoding="utf-8")
        path.chmod(0o755)
    swapfile_path = tmp_path / "swapfile"
    environment = dict(os.environ)
    environment["PATH"] = f"{stub_directory}:{environment['PATH']}"
    environment["CALL_LOG"] = str(call_log)
    result = subprocess.run(
        [sys.executable, str(_PROGRAM_PATH), *_arguments(_config(tmp_path))],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout.strip().splitlines()[-1])["changed"] is True
    calls = call_log.read_text(encoding="utf-8")
    assert f"chattr +C {swapfile_path}.probe" in calls
    assert f"fallocate -l 512K {swapfile_path}.probe" in calls
    assert f"chattr +C {swapfile_path}" in calls
    assert f"fallocate -l 1M {swapfile_path}" in calls
    assert f"swapon {swapfile_path}" in calls
    assert not Path(f"{swapfile_path}.probe").exists()
