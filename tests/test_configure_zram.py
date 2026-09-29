"""Unit tests for the zram setup program of the zram_service task.

The program lives under task_data/ and is not part of the package, so it is
loaded through importlib.util (the same way the other deployed programs are
loaded in the tests). Every command the program runs is replaced by a recording
double and the sysfs mirror lives in a temporary tree, so no test creates,
formats or removes a zram device on this machine.

The read-to-add interface of kernel 7.0 creates one device on every read of
hot_add, which a plain file cannot do, so the mirror stands in for it in the
same way the kernel behaves. The live mechanism was proved by hand on the
machine and is not repeated here.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from support import REPO_ROOT

_PROGRAM_PATH = REPO_ROOT / "task_data" / "zram_service" / "configure_zram.py"


def _load_program() -> types.ModuleType:
    """Load the deployed program as a module for the in-process tests.

    The module is registered under its name before it is executed, because the
    dataclasses of the program ask sys.modules for their own module while the
    class is built.
    """

    spec = importlib.util.spec_from_file_location(
        "configure_zram_under_test", _PROGRAM_PATH
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

# 16 GiB RAM on 2 cores.
RAM_KIB = 16 * 1024 * 1024
CORES = 2


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
    with pytest.raises(program.ZramError) as raised:
        _REAL_TOOL_PATH("pyntara-no-such-tool")
    assert "pyntara-no-such-tool" in str(raised.value)


def _device_count(sys_block: Path) -> int:
    """Number of zram devices present in the sysfs mirror."""

    if not sys_block.is_dir():
        return 0
    return len([path for path in sys_block.iterdir() if path.name.startswith("zram")])


def _create_device(
    sys_block: Path, index: int, configured_size: int | None = None
) -> None:
    """Create one device directory with a kernel default or a set state."""

    device = sys_block / f"zram{index}"
    device.mkdir(parents=True, exist_ok=True)
    if configured_size is None:
        (device / "comp_algorithm").write_text(
            "lzo lzo-rle [lzo-rle] zstd", encoding="utf-8"
        )
        (device / "disksize").write_text("0", encoding="utf-8")
    else:
        (device / "comp_algorithm").write_text(
            "lzo lzo-rle [zstd] zstd", encoding="utf-8"
        )
        (device / "disksize").write_text(str(configured_size), encoding="utf-8")
    (device / "reset").write_text("0", encoding="utf-8")


class _Stat:
    """Minimal stand-in for os.stat_result; only st_mode is read."""

    def __init__(self, st_mode: int) -> None:
        self.st_mode = st_mode


class FakeHotAdd:
    """hot_add as the kernel exposes it: a read or a write creates a device.

    The attribute mode tells the two interfaces apart: 0400 read-only for the
    read-to-add interface of kernel 7.0, 0200 write-only for the write interface
    of older kernels. A read on the read interface creates one device and
    returns its id, which a plain file cannot do, so this mirror stands in for
    the kernel of the machine.
    """

    def __init__(self, sys_block: Path, *, read_interface: bool = True) -> None:
        self.sys_block = sys_block
        self.read_interface = read_interface
        self.read_count = 0

    def stat(self) -> _Stat:
        return _Stat(0o400 if self.read_interface else 0o200)

    def read_text(self, encoding: str = "utf-8") -> str:
        del encoding
        if not self.read_interface:
            raise PermissionError(13, "Permission denied")
        index = _device_count(self.sys_block)
        _create_device(self.sys_block, index)
        self.read_count += 1
        return f"{index}\n"


class Sysfs:
    """Temporary mirror of the kernel zram interface of one test."""

    def __init__(self, sys_block: Path, control: Path) -> None:
        self.sys_block = sys_block
        self.control = control
        self.hot_add: FakeHotAdd | None = None

    def create_device(self, index: int, *, configured_size: int | None = None) -> None:
        _create_device(self.sys_block, index, configured_size)

    def device_count(self) -> int:
        return _device_count(self.sys_block)

    def hot_remove_path(self) -> Path:
        return self.control / "hot_remove"


@pytest.fixture
def sysfs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Sysfs:
    """Point the program at a temporary kernel interface mirror."""

    sys_block = tmp_path / "sys" / "block"
    sys_block.mkdir(parents=True)
    control = tmp_path / "sys" / "class" / "zram-control"
    control.mkdir(parents=True)
    (control / "hot_remove").write_text("", encoding="utf-8")
    monkeypatch.setattr(program, "SYS_BLOCK_PATH", sys_block)
    return Sysfs(sys_block, control)


def _config(
    tmp_path: Path,
    *,
    force: bool = False,
    cores: int = CORES,
    ram_kib: int = RAM_KIB,
    memory_fraction_percent: int = 96,
    alignment_bytes: int = 4096,
) -> Any:
    """The configuration of one run, with the kernel files of the test.

    The memory and the core count come from files the test writes, so the target
    of every run is known and no test reads the kernel files of the machine that
    runs the tests.
    """

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {ram_kib} kB\n", encoding="utf-8")
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "".join(f"processor : {index}\n" for index in range(cores)),
        encoding="utf-8",
    )
    return program.Config(
        meminfo_path=meminfo,
        meminfo_total_key="MemTotal:",
        cpuinfo_path=cpuinfo,
        cpuinfo_processor_key="processor",
        compressor="zstd",
        swap_priority=1111,
        memory_fraction_percent=memory_fraction_percent,
        percent_scale=100,
        fallback_cpu_count=8,
        alignment_bytes=alignment_bytes,
        bytes_per_kib=1024,
        module_name="zram",
        reset_busy_attempts=5,
        reset_busy_retry_delay_seconds=0.0,
        command_timeout_seconds=30.0,
        force=force,
    )


def _config_with(config: Any, **changes: Any) -> Any:
    """A copy of a configuration with the named fields replaced."""

    return program.Config(**{**config.__dict__, **changes})


def _target(ram_kib: int, cores: int) -> tuple[int, int]:
    """Target (device_count, per_device_bytes) for the shipped factors."""

    total_bytes = ram_kib * 1024 * 96 // 100
    return cores, total_bytes // cores // 4096 * 4096


def _answered(
    command: list[str], returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    """One canned answer of the command double."""

    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    sysfs: Sysfs,
    *,
    read_interface: bool = True,
    active: set[str] | None = None,
    fail: Callable[[list[str]], bool] | None = None,
) -> tuple[list[list[str]], list[tuple[Path, str]], set[str]]:
    """Install the command double and the sysfs write double.

    modprobe creates zram0 like the kernel does, hot_add appends devices past
    the existing count, hot_remove deletes one device, swapon and swapoff update
    the active set and the swap listing reports it. A command matched by fail
    answers with a nonzero exit code, as a failed command would. Writing
    comp_algorithm stores the value in bracketed form, as the kernel reports it.
    """

    active_paths = set() if active is None else active
    calls: list[list[str]] = []
    writes: list[tuple[Path, str]] = []
    hot_add = FakeHotAdd(sysfs.sys_block, read_interface=read_interface)
    sysfs.hot_add = hot_add
    hot_remove = sysfs.hot_remove_path()
    monkeypatch.setattr(program, "HOT_ADD_PATH", hot_add)
    monkeypatch.setattr(program, "HOT_REMOVE_PATH", hot_remove)

    def run(
        command: list[str], timeout_seconds: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        calls.append(list(command))
        if fail is not None and fail(command):
            return _answered(command, 1, "", "command failed")
        if command[0] == "modprobe":
            if sysfs.device_count() == 0:
                sysfs.create_device(0)
            return _answered(command, 0)
        if command[0] == "swapon" and command[1] == "--show":
            output = "".join(
                f"{path} partition 1G 0B 1111\n" for path in sorted(active_paths)
            )
            return _answered(command, 0, output)
        if command[0] == "swapoff":
            active_paths.discard(command[1])
            return _answered(command, 0)
        if command[0] == "swapon":
            active_paths.add(command[-1])
            return _answered(command, 0)
        return _answered(command, 0)

    def write_sysfs(path: Path, value: str) -> None:
        writes.append((path, value))
        if path is hot_add:
            # Write interface of older kernels: one write creates one device.
            sysfs.create_device(sysfs.device_count())
            return
        if path == hot_remove:
            index = int(value)
            shutil.rmtree(sysfs.sys_block / f"zram{index}")
            active_paths.discard(f"/dev/zram{index}")
            return
        if path.name == "comp_algorithm":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"lzo lzo-rle [{value}]", encoding="utf-8")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    monkeypatch.setattr(program, "_run", run)
    monkeypatch.setattr(program, "_write_sysfs", write_sysfs)
    return calls, writes, active_paths


def test_calculate_devices_uses_the_memory_fraction_and_the_core_count(
    tmp_path: Path,
) -> None:
    # 16 GiB RAM on 2 cores: two devices, each carrying half of 96 percent of
    # RAM rounded down to the 4096-byte zram page size.
    device_count, per_device_bytes = program._calculate_devices(
        RAM_KIB, CORES, _config(tmp_path)
    )
    assert device_count == 2
    total_bytes = RAM_KIB * 1024 * 96 // 100
    assert per_device_bytes * 2 <= total_bytes
    assert per_device_bytes % 4096 == 0
    assert per_device_bytes * 2 >= total_bytes - 2 * 4096


def test_the_target_follows_the_declared_factors(tmp_path: Path) -> None:
    config = _config(tmp_path, memory_fraction_percent=50, alignment_bytes=1024)
    device_count, per_device_bytes = program._calculate_devices(RAM_KIB, CORES, config)
    assert device_count == 2
    expected = RAM_KIB * 1024 * 50 // 100 // 2 // 1024 * 1024
    assert per_device_bytes == expected


def test_read_cpu_count_and_the_fallback(tmp_path: Path) -> None:
    config = _config(tmp_path, cores=3)
    assert program._read_cpu_count(config) == (3, False)
    missing = _config_with(config, cpuinfo_path=tmp_path / "absent-cpuinfo")
    assert program._read_cpu_count(missing) == (8, True)


def test_a_missing_meminfo_ends_the_run(tmp_path: Path) -> None:
    config = _config_with(_config(tmp_path), meminfo_path=tmp_path / "absent-meminfo")
    with pytest.raises(program.ZramError):
        program._read_ram_kib(config)


def test_configure_creates_every_device_and_activates_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    device_count, per_device_bytes = _target(RAM_KIB, CORES)
    calls, _writes, active = _install_fakes(monkeypatch, sysfs)
    outcome = program.configure_zram(_config(tmp_path))
    assert outcome.changed is True
    assert outcome.warnings == ()
    assert ["mkswap", "/dev/zram0"] in calls
    assert ["mkswap", "/dev/zram1"] in calls
    assert ["swapon", "--priority", "1111", "/dev/zram0"] in calls
    assert ["swapon", "--priority", "1111", "/dev/zram1"] in calls
    assert active == {"/dev/zram0", "/dev/zram1"}
    assert sysfs.device_count() == device_count
    assert program._read_disksize(0, "zram") == per_device_bytes
    assert program._read_active_algorithm(0, "zram") == "zstd"


def test_configure_uses_the_write_interface_of_older_kernels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    # hot_add is write-only: every write creates one device, and the program
    # detects that interface at run time.
    calls, writes, active = _install_fakes(monkeypatch, sysfs, read_interface=False)
    outcome = program.configure_zram(_config(tmp_path))
    assert outcome.changed is True
    assert any(path is sysfs.hot_add for path, _ in writes)
    assert active == {"/dev/zram0", "/dev/zram1"}
    assert ["mkswap", "/dev/zram1"] in calls


def test_a_reached_target_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    device_count, per_device_bytes = _target(RAM_KIB, CORES)
    for index in range(device_count):
        sysfs.create_device(index, configured_size=per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    _calls, writes, _active = _install_fakes(monkeypatch, sysfs, active=active)
    outcome = program.configure_zram(_config(tmp_path))
    assert outcome.changed is False
    assert writes == []


def test_extra_devices_are_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    _device_count, per_device_bytes = _target(RAM_KIB, CORES)
    for index in range(4):
        sysfs.create_device(index, configured_size=per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(4)}
    calls, writes, active_after = _install_fakes(monkeypatch, sysfs, active=active)
    outcome = program.configure_zram(_config(tmp_path))
    assert outcome.changed is True
    assert ["swapoff", "/dev/zram2"] in calls
    assert ["swapoff", "/dev/zram3"] in calls
    assert (sysfs.hot_remove_path(), "2") in writes
    assert (sysfs.hot_remove_path(), "3") in writes
    assert active_after == {"/dev/zram0", "/dev/zram1"}
    assert sysfs.device_count() == CORES


def test_force_reconfigures_a_reached_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    device_count, per_device_bytes = _target(RAM_KIB, CORES)
    for index in range(device_count):
        sysfs.create_device(index, configured_size=per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    calls, _writes, _active = _install_fakes(monkeypatch, sysfs, active=set(active))
    outcome = program.configure_zram(_config(tmp_path, force=True))
    assert outcome.changed is True
    assert ["swapoff", "/dev/zram0"] in calls
    assert ["mkswap", "/dev/zram0"] in calls


def test_a_reset_retries_on_a_transient_busy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    device_count, per_device_bytes = _target(RAM_KIB, CORES)
    for index in range(device_count):
        sysfs.create_device(index, configured_size=per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    _calls, _writes, _active = _install_fakes(monkeypatch, sysfs, active=set(active))
    reset_path = sysfs.sys_block / "zram0" / "reset"
    plain_write = program._write_sysfs
    attempts = {"count": 0}

    def busy_once(path: Path, value: str) -> None:
        if path == reset_path:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OSError(16, "Device or resource busy", str(path))
        plain_write(path, value)

    monkeypatch.setattr(program, "_write_sysfs", busy_once)
    outcome = program.configure_zram(_config(tmp_path, force=True))
    assert outcome.changed is True
    assert attempts["count"] == 2


def test_a_reset_that_stays_busy_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    device_count, per_device_bytes = _target(RAM_KIB, CORES)
    for index in range(device_count):
        sysfs.create_device(index, configured_size=per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    _calls, _writes, _active = _install_fakes(monkeypatch, sysfs, active=set(active))
    reset_path = sysfs.sys_block / "zram0" / "reset"
    plain_write = program._write_sysfs
    attempts = {"count": 0}

    def always_busy(path: Path, value: str) -> None:
        if path == reset_path:
            attempts["count"] += 1
            raise OSError(16, "Device or resource busy", str(path))
        plain_write(path, value)

    monkeypatch.setattr(program, "_write_sysfs", always_busy)
    outcome = program.configure_zram(_config(tmp_path, force=True))
    assert any("cannot reset zram0" in warning for warning in outcome.warnings)
    assert attempts["count"] == 5


def test_a_failed_format_of_one_device_keeps_the_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    _calls, _writes, active = _install_fakes(
        monkeypatch, sysfs, fail=lambda command: command[0] == "mkswap"
    )
    outcome = program.configure_zram(_config(tmp_path))
    assert any("zram0 setup failed" in warning for warning in outcome.warnings)
    assert any("zram1 setup failed" in warning for warning in outcome.warnings)
    assert active == set()


def test_a_module_that_cannot_load_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    _calls, _writes, _active = _install_fakes(
        monkeypatch, sysfs, fail=lambda command: command[0] == "modprobe"
    )
    outcome = program.configure_zram(_config(tmp_path))
    assert any("cannot load zram module" in warning for warning in outcome.warnings)


def test_a_zero_device_size_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sysfs: Sysfs
) -> None:
    # A fraction that rounds the device size down to zero is a misconfiguration
    # the program refuses instead of writing a zero disksize.
    _install_fakes(monkeypatch, sysfs)
    with pytest.raises(program.ZramError):
        program.configure_zram(_config(tmp_path, memory_fraction_percent=0))


def _arguments(tmp_path: Path, *, force: bool = False) -> list[str]:
    """The command line of the program with the files of the test."""

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {RAM_KIB} kB\n", encoding="utf-8")
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "".join(f"processor : {index}\n" for index in range(CORES)),
        encoding="utf-8",
    )
    arguments = [
        "--meminfo",
        str(meminfo),
        "--meminfo-total-key",
        "MemTotal:",
        "--cpuinfo",
        str(cpuinfo),
        "--cpuinfo-processor-key",
        "processor",
        "--compressor",
        "zstd",
        "--swap-priority",
        "1111",
        "--memory-fraction-percent",
        "96",
        "--percent-scale",
        "100",
        "--fallback-cpu-count",
        "8",
        "--alignment-bytes",
        "4096",
        "--bytes-per-kib",
        "1024",
        "--module-name",
        "zram",
        "--reset-busy-attempts",
        "5",
        "--reset-busy-retry-delay-seconds",
        "0",
        "--command-timeout-seconds",
        "30",
    ]
    if force:
        arguments.append("--force")
    return arguments


def test_main_prints_the_result_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sysfs: Sysfs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fakes(monkeypatch, sysfs)
    exit_code = program.main(_arguments(tmp_path))
    assert exit_code == 0
    outcome = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert outcome == {
        "changed": True,
        "skipped_reason": None,
        "error": None,
        "warnings": [],
    }


def test_main_reports_an_error_with_exit_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sysfs: Sysfs,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fakes(monkeypatch, sysfs)
    arguments = _arguments(tmp_path)
    arguments[arguments.index("--meminfo") + 1] = str(tmp_path / "absent-meminfo")
    exit_code = program.main(arguments)
    assert exit_code == 1
    outcome = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert outcome["error"]
    assert outcome["changed"] is False
