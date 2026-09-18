"""Unit tests for the zram_service task.

All external resources (meminfo, cpuinfo, sysfs, subprocess, filesystem
paths) are mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The unit template is rendered from a
fixture, so the tests never read the repository template. The values of the
section come from the values module, and the engine values still come from the
declared module, and the task reads them through it.
"""

from __future__ import annotations

import errno
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

import pytest
from support import FakeProc as _FakeProc
from support import make_context

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

# 16 GiB RAM on 2 cores; the total target is 96 percent of RAM.
RAM_KIB = 16 * 1024 * 1024


@pytest.fixture(autouse=True)
def _point_the_unit_directory_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own systemd unit directory.

    The directory is a declared value of the engine, so the fixture points it
    at the temporary directory of the test and the shipped value comes back
    afterwards, so no test writes into /etc/systemd/system.
    """

    monkeypatch.setattr(engine_values, "SYSTEMD_UNIT_DIR", tmp_path / "systemd")


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
) -> Context:
    """Context with the safe defaults the engine fills in a real run.

    Every value is read from a values module, which a test patches where it
    needs another one. The systemd unit directory and the repository
    root are the temporary tree, because the task writes the unit there and reads
    the template from there.
    """

    return make_context(
        task_name="zram_service",
        install_mode="server",
        force_tasks=frozenset({"zram_service"}) if force else frozenset(),
        task_data_root=tmp_path,
        repo_root=tmp_path,
        skip_apt_update=True,
    )


def test_hot_add_read_interface_uses_the_configured_bit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The attribute mode is told apart by the configured bit: the fixture
    # exposes the owner-read bit, so the configured bit reports the
    # read-to-add interface while the owner-write bit reports write-to-add
    # for the very same attribute.
    _install_fixtures(monkeypatch, tmp_path, read_interface=True)
    assert zram_service._hot_add_read_interface() is True
    monkeypatch.setattr(values, "HOT_ADD_READABLE_MODE_BIT", 0o200)
    assert zram_service._hot_add_read_interface() is False


def _target(tmp_path: Path) -> tuple[int, int]:
    """Target (device_count, per_device_bytes) with the shipped values.

    The byte factor and the percent scale are declared engine values, and the
    section values are read by the task from its module.
    """

    return zram_service._calculate_devices(
        RAM_KIB,
        2,
        engine_values.BYTES_PER_KIB,
        engine_values.PERCENT_SCALE,
    )


class ZramFixtures(TypedDict):
    """Temporary sysfs mirror and template paths."""

    sys_block: Path
    hot_add: _FakeHotAdd
    hot_remove: Path
    template: Path


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    ram_kib: int = RAM_KIB,
    cores: int = 2,
    with_cpuinfo: bool = True,
    read_interface: bool = True,
) -> ZramFixtures:
    """Point the task at temporary fixtures; return the fixture paths."""

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {ram_kib} kB\n", encoding="utf-8")
    monkeypatch.setattr(zram_service, "MEMINFO_PATH", meminfo)
    cpuinfo_path = tmp_path / "cpuinfo"
    if with_cpuinfo:
        cpuinfo_path.write_text(
            "".join(f"processor : {i}\n" for i in range(cores)),
            encoding="utf-8",
        )
    monkeypatch.setattr(zram_service, "CPUINFO_PATH", cpuinfo_path)
    template = tmp_path / "task_data" / "zram_service" / "zram.service"
    template.parent.mkdir(parents=True)
    template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    sys_block = tmp_path / "sys" / "block"
    sys_block.mkdir(parents=True)
    monkeypatch.setattr(zram_service, "SYS_BLOCK_PATH", sys_block)
    hot_remove = tmp_path / "sys" / "class" / "zram-control" / "hot_remove"
    hot_remove.parent.mkdir(parents=True)
    hot_remove.write_text("", encoding="utf-8")
    hot_add = _FakeHotAdd(
        sys_block,
        path_text=str(hot_remove.parent / "hot_add"),
        read_interface=read_interface,
    )
    monkeypatch.setattr(zram_service, "ZRAM_HOT_ADD_PATH", hot_add)
    monkeypatch.setattr(zram_service, "ZRAM_HOT_REMOVE_PATH", hot_remove)
    return {
        "sys_block": sys_block,
        "hot_add": hot_add,
        "hot_remove": hot_remove,
        "template": template,
    }


def _create_device(sys_block: Path, index: int) -> None:
    """Create one zram device directory with the kernel default state."""

    device = sys_block / f"zram{index}"
    device.mkdir(parents=True, exist_ok=True)
    (device / "comp_algorithm").write_text(
        "lzo lzo-rle [lzo-rle] zstd", encoding="utf-8"
    )
    (device / "disksize").write_text("0", encoding="utf-8")
    (device / "reset").write_text("0", encoding="utf-8")


def _configure_device(sys_block: Path, index: int, size_bytes: int) -> None:
    """Configure one device to the target state."""

    device = sys_block / f"zram{index}"
    device.mkdir(parents=True, exist_ok=True)
    (device / "comp_algorithm").write_text("lzo lzo-rle [zstd] zstd", encoding="utf-8")
    (device / "disksize").write_text(str(size_bytes), encoding="utf-8")
    (device / "reset").write_text("0", encoding="utf-8")


def _device_count(sys_block: Path) -> int:
    """Number of zram devices present in the sysfs fixture."""

    if not sys_block.is_dir():
        return 0
    return len([path for path in sys_block.iterdir() if path.name.startswith("zram")])


class _Stat:
    """Minimal stand-in for os.stat_result; only st_mode is read."""

    def __init__(self, st_mode: int) -> None:
        self.st_mode = st_mode


class _FakeHotAdd:
    """hot_add as kernel 7.0 exposes it: reading creates a device.

    stat reports the attribute mode: 0400 read-only for the read-to-add
    interface, 0200 write-only for the write interface of older kernels.
    A read on the read interface creates one device and returns its id.
    """

    def __init__(
        self,
        sys_block: Path,
        *,
        path_text: str,
        read_interface: bool = True,
    ) -> None:
        self.sys_block = sys_block
        self.path_text = path_text
        self.read_interface = read_interface
        self.read_count = 0

    def __str__(self) -> str:
        """The path the attribute has on the machine the fixture stands for.

        The rendered unit carries the path of the attribute, so the fake
        reports the path of its fixture instead of the repr of the object.
        """

        return self.path_text

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


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    fixtures: ZramFixtures,
    *,
    enabled: bool,
    active: set[str],
    fail: Callable[[list[str]], bool] | None = None,
) -> tuple[list[list[str]], list[tuple[Path, str]], set[str]]:
    """Install subprocess and sysfs fakes; return (calls, writes, active).

    modprobe creates zram0 like the kernel does, hot_add appends devices
    past the existing count, hot_remove deletes one device, swapon and
    swapoff update the active set, swapon --show reports the active set,
    systemctl is-enabled answers from the enabled flag and every other
    command succeeds. A command matched by fail raises CalledProcessError,
    as a nonzero exit would. Writing comp_algorithm stores the value in
    bracketed form, as the kernel reports it.
    """

    sys_block = fixtures["sys_block"]
    hot_add = fixtures["hot_add"]
    hot_remove = fixtures["hot_remove"]
    calls: list[list[str]] = []
    writes: list[tuple[Path, str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if fail is not None and fail(command):
            raise subprocess.CalledProcessError(1, command)
        if command[0] == "modprobe":
            _create_device(sys_block, 0)
            return _FakeProc(0)
        if command[0] == "swapon" and command[1] == "--show":
            output = "".join(
                f"{path} partition 1G 0B 1111\n" for path in sorted(active)
            )
            return _FakeProc(0, output)
        if command[0] == "swapoff":
            active.discard(command[1])
            return _FakeProc(0)
        if command[0] == "swapon":
            active.add(command[-1])
            return _FakeProc(0)
        if command[0] == "mkswap":
            return _FakeProc(0)
        if command[0] == "systemctl" and command[1] == "is-enabled":
            if enabled:
                return _FakeProc(0, "enabled\n")
            return _FakeProc(1, "disabled")
        return _FakeProc(0)

    def fake_write_sysfs(path: Path, value: str) -> None:
        writes.append((path, value))
        if path is hot_add:
            # Write interface of older kernels: one write creates one device.
            _create_device(sys_block, _device_count(sys_block))
            return
        if path == hot_remove:
            index = int(value)
            shutil.rmtree(sys_block / f"zram{index}")
            active.discard(f"/dev/zram{index}")
            return
        if path.name == "comp_algorithm":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"lzo lzo-rle [{value}]", encoding="utf-8")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    monkeypatch.setattr(zram_service, "_write_sysfs", fake_write_sysfs)
    return calls, writes, active


def _expected_unit(
    fixtures: ZramFixtures,
    device_count: int,
    per_device_bytes: int,
    *,
    read_interface: bool = True,
) -> str:
    """The unit file the task must render for the given target.

    The paths of the block are the paths of the fixtures, because the
    fixture directory stands for the kernel file systems of the machine.
    """

    hot_add = str(fixtures["hot_add"])
    sys_block = fixtures["sys_block"]
    lines = ["ExecStart=/bin/sh -c 'modprobe zram || true'"]
    for index in range(1, device_count):
        if read_interface:
            lines.append(f"ExecStart=/bin/cat {hot_add}")
        else:
            lines.append(f"ExecStart=/bin/sh -c 'echo 1 > {hot_add}'")
    for index in range(device_count):
        lines.append(
            f"ExecStart=/bin/sh -c 'echo zstd > {sys_block}/zram{index}/comp_algorithm'"
        )
        lines.append(
            f"ExecStart=/bin/sh -c 'echo {per_device_bytes} > "
            f"{sys_block}/zram{index}/disksize'"
        )
        lines.append(f"ExecStart=/sbin/mkswap /dev/zram{index}")
        lines.append(f"ExecStart=/sbin/swapon --priority 1111 /dev/zram{index}")
    return UNIT_TEMPLATE.replace("$exec_lines", "\n".join(lines))


def test_calculate_devices_uses_96_percent_and_core_count() -> (
    None
):  # 16 GiB RAM on 2 cores: two devices, each carrying half of 96 percent
    # of RAM rounded down to the 4096-byte zram page size.
    device_count, per_device_bytes = zram_service._calculate_devices(
        RAM_KIB,
        2,
        engine_values.BYTES_PER_KIB,
        engine_values.PERCENT_SCALE,
    )
    assert device_count == 2
    total_bytes = RAM_KIB * 1024 * 96 // 100
    assert per_device_bytes * 2 <= total_bytes
    assert per_device_bytes % 4096 == 0
    # Alignment costs less than one page per device.
    assert per_device_bytes * 2 >= total_bytes - 2 * 4096


def test_device_target_follows_the_engine_factors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The byte factor and the percent scale are declared engine values, so
    # the fixture points them at other numbers and the written unit carries
    # the target counted with them.
    monkeypatch.setattr(engine_values, "BYTES_PER_KIB", 1000)
    monkeypatch.setattr(engine_values, "PERCENT_SCALE", 50)
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, fixtures, enabled=False, active=set())
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    expected_total = RAM_KIB * 1000 * values.MEMORY_FRACTION_PERCENT // 50
    per_device_bytes = (
        expected_total // 2 // values.ALIGNMENT_BYTES * values.ALIGNMENT_BYTES
    )
    unit = (tmp_path / "systemd" / "zram.service").read_text(encoding="utf-8")
    assert unit == _expected_unit(fixtures, 2, per_device_bytes)


def test_read_cpu_count_returns_processor_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor : 0\nprocessor : 1\n", encoding="utf-8")
    monkeypatch.setattr(zram_service, "CPUINFO_PATH", cpuinfo)
    assert zram_service._read_cpu_count() == (2, False)


def test_the_kernel_line_names_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The names of the two kernel file lines the task reads are values: another
    # name in the module is the line the task counts.
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("Total-RAM:       8192 kB\n", encoding="utf-8")
    monkeypatch.setattr(zram_service, "MEMINFO_PATH", meminfo)
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("core : 0\ncore : 1\ncore : 2\n", encoding="utf-8")
    monkeypatch.setattr(zram_service, "CPUINFO_PATH", cpuinfo)
    monkeypatch.setattr(common_values, "MEMINFO_TOTAL_KEY", "Total-RAM:")
    monkeypatch.setattr(values, "CPUINFO_PROCESSOR_KEY", "core")
    assert zram_service._read_ram_kib() == 8192
    assert zram_service._read_cpu_count() == (3, False)


def test_read_cpu_count_falls_back_to_8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A missing cpuinfo file means the spec fallback of 8, flagged.
    monkeypatch.setattr(zram_service, "CPUINFO_PATH", tmp_path / "cpuinfo")
    assert zram_service._read_cpu_count() == (8, True)


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Both devices exist at the computed size with zstd, are active and the
    # service is enabled: the task skips and runs only the status queries.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    device_count, per_device_bytes = _target(tmp_path)
    for index in range(device_count):
        _configure_device(fixtures["sys_block"], index, per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    calls, writes, _ = _install_fake(monkeypatch, fixtures, enabled=True, active=active)
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert all(call[0] in ("swapon", "systemctl") for call in calls)
    assert writes == []


def test_the_device_name_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The name of a device is the configured module name with its index:
    # another module name in the module is the /sys/block entry the task
    # counts and the swap device path it formats and activates.
    sys_block = tmp_path / "sys" / "block"
    (sys_block / "myzram0").mkdir(parents=True)
    (sys_block / "myzram1").mkdir()
    (sys_block / "zram7").mkdir()
    monkeypatch.setattr(zram_service, "SYS_BLOCK_PATH", sys_block)
    monkeypatch.setattr(values, "MODULE_NAME", "myzram")
    assert zram_service._existing_device_indices(values.MODULE_NAME) == [0, 1]
    assert zram_service._existing_device_count(values.MODULE_NAME) == 2
    assert zram_service._device_path(values.MODULE_NAME, 4) == "/dev/myzram4"
    assert zram_service._device_name(values.MODULE_NAME, 4) == "myzram4"


def test_creates_devices_and_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Nothing is configured: the task loads the module, creates the missing
    # device, configures both, activates them, renders the unit template
    # and enables the service.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    device_count, per_device_bytes = _target(tmp_path)
    calls, writes, active = _install_fake(
        monkeypatch, fixtures, enabled=False, active=set()
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert ["modprobe", "zram"] in calls
    assert ["mkswap", "/dev/zram0"] in calls
    assert ["mkswap", "/dev/zram1"] in calls
    assert ["swapon", "--priority", "1111", "/dev/zram0"] in calls
    assert ["swapon", "--priority", "1111", "/dev/zram1"] in calls
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "zram.service"] in calls
    # modprobe made zram0, one read of hot_add makes zram1.
    assert fixtures["hot_add"].read_count == 1
    assert not any(path is fixtures["hot_add"] for path, _ in writes)
    assert active == {"/dev/zram0", "/dev/zram1"}
    unit = tmp_path / "systemd" / "zram.service"
    assert unit.read_text(encoding="utf-8") == _expected_unit(
        fixtures, device_count, per_device_bytes, read_interface=True
    )


def test_fallback_cpu_count_uses_8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Missing cpuinfo: the spec fallback of 8 devices is used and reported.
    fixtures = _install_fixtures(monkeypatch, tmp_path, with_cpuinfo=False)
    calls, _, _ = _install_fake(monkeypatch, fixtures, enabled=False, active=set())
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert ["mkswap", "/dev/zram7"] in calls
    # modprobe made zram0, seven reads of hot_add make zram1..zram7.
    assert fixtures["hot_add"].read_count == 7
    assert "8 devices" in (result.message or "")
    captured = capsys.readouterr()
    assert "using fallback 8" in captured.out


def test_removes_extra_devices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Four devices exist, the target is two: the extras are swapped off,
    # removed and never reconfigured.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    _, per_device_bytes = _target(tmp_path)
    for index in range(4):
        _configure_device(fixtures["sys_block"], index, per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(4)}
    calls, writes, active_after = _install_fake(
        monkeypatch, fixtures, enabled=False, active=active
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert ["swapoff", "/dev/zram2"] in calls
    assert ["swapoff", "/dev/zram3"] in calls
    assert (fixtures["hot_remove"], "2") in writes
    assert (fixtures["hot_remove"], "3") in writes
    assert active_after == {"/dev/zram0", "/dev/zram1"}
    assert fixtures["hot_add"].read_count == 0
    assert not any(path is fixtures["hot_add"] for path, _ in writes)


def test_reset_retries_on_transient_busy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The first reset of zram0 hits a transient EBUSY, as when a udev
    # probe holds the device open for a moment: the task retries and
    # completes the teardown.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    device_count, per_device_bytes = _target(tmp_path)
    for index in range(device_count):
        _configure_device(fixtures["sys_block"], index, per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    _, _, _ = _install_fake(monkeypatch, fixtures, enabled=True, active=set(active))
    reset_path = fixtures["sys_block"] / "zram0" / "reset"
    plain_write = zram_service._write_sysfs
    attempts = {"count": 0}

    def busy_once(path: Path, value: str) -> None:
        if path == reset_path:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OSError(errno.EBUSY, "Device or resource busy", str(path))
        plain_write(path, value)

    monkeypatch.setattr(zram_service, "_write_sysfs", busy_once)
    monkeypatch.setattr(values, "RESET_BUSY_RETRY_DELAY_SECONDS", 0.0)
    result = zram_service.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert attempts["count"] == 2


def test_reset_failure_after_retries_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The reset of zram0 stays busy across every attempt: the task reports
    # the reason after the configured number of retries and configures
    # the device anyway.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    device_count, per_device_bytes = _target(tmp_path)
    for index in range(device_count):
        _configure_device(fixtures["sys_block"], index, per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    _, _, _ = _install_fake(monkeypatch, fixtures, enabled=True, active=set(active))
    reset_path = fixtures["sys_block"] / "zram0" / "reset"
    plain_write = zram_service._write_sysfs
    attempts = {"count": 0}

    def always_busy(path: Path, value: str) -> None:
        if path == reset_path:
            attempts["count"] += 1
            raise OSError(errno.EBUSY, "Device or resource busy", str(path))
        plain_write(path, value)

    monkeypatch.setattr(zram_service, "_write_sysfs", always_busy)
    monkeypatch.setattr(values, "RESET_BUSY_ATTEMPTS", 3)
    monkeypatch.setattr(values, "RESET_BUSY_RETRY_DELAY_SECONDS", 0.0)
    result = zram_service.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert result.changed is True
    assert any("cannot reset zram0" in warning for warning in result.warnings)
    assert attempts["count"] == 3


def test_force_mode_reconfigures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is already configured, but the task is forced: it swaps
    # the devices off, resets them and configures them again.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    device_count, per_device_bytes = _target(tmp_path)
    for index in range(device_count):
        _configure_device(fixtures["sys_block"], index, per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    calls, _, _ = _install_fake(monkeypatch, fixtures, enabled=True, active=set(active))
    result = zram_service.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert result.changed is True
    assert ["swapoff", "/dev/zram0"] in calls
    assert ["mkswap", "/dev/zram0"] in calls
    assert ["systemctl", "enable", "zram.service"] in calls


def test_mkswap_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # mkswap fails on every device: each of them is reported, no
    # activation happens and the task completes.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    calls, _, _ = _install_fake(
        monkeypatch,
        fixtures,
        enabled=False,
        active=set(),
        fail=lambda command: command[0] == "mkswap",
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert any("zram0 setup failed" in warning for warning in result.warnings)
    assert any("zram1 setup failed" in warning for warning in result.warnings)
    assert not any(call[0] == "swapon" and "--priority" in call for call in calls)


def test_modprobe_failure_reports_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The module cannot load: the task reports the reason and keeps the
    # run going, so the failure of one step never hides the others.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    calls, _, _ = _install_fake(
        monkeypatch,
        fixtures,
        enabled=False,
        active=set(),
        fail=lambda command: command[0] == "modprobe",
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert any("cannot load zram module" in warning for warning in result.warnings)
    assert any(call[0] == "mkswap" for call in calls)


def test_missing_template_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit template is missing: the devices are configured, the
    # service file is skipped alone and the task completes.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    fixtures["template"].unlink()
    calls, _, _ = _install_fake(monkeypatch, fixtures, enabled=False, active=set())
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert any("template" in warning for warning in result.warnings)
    assert ["mkswap", "/dev/zram0"] in calls
    assert not any(call[:2] == ["systemctl", "enable"] for call in calls)


def test_systemctl_enable_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # systemctl enable fails after the devices were configured: the task
    # reports the reason, keeps the change and completes.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    calls, _, _ = _install_fake(
        monkeypatch,
        fixtures,
        enabled=False,
        active=set(),
        fail=lambda command: command[:2] == ["systemctl", "enable"],
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert any("systemd setup failed" in warning for warning in result.warnings)
    assert ["mkswap", "/dev/zram0"] in calls


def test_write_interface_creates_devices_and_renders_write_unit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # On an older kernel hot_add is write-only: the task detects the write
    # interface, creates the missing device by writing and renders the
    # boot unit with the echo command.
    fixtures = _install_fixtures(monkeypatch, tmp_path, read_interface=False)
    device_count, per_device_bytes = _target(tmp_path)
    calls, writes, active = _install_fake(
        monkeypatch, fixtures, enabled=False, active=set()
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert ["mkswap", "/dev/zram1"] in calls
    assert fixtures["hot_add"].read_count == 0
    assert any(path is fixtures["hot_add"] for path, _ in writes)
    assert active == {"/dev/zram0", "/dev/zram1"}
    unit = tmp_path / "systemd" / "zram.service"
    assert unit.read_text(encoding="utf-8") == _expected_unit(
        fixtures, device_count, per_device_bytes, read_interface=False
    )


def test_commands_and_unit_lines_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The commands of the run and the lines of the ExecStart block are values:
    # another load command, another activation call and another algorithm line
    # are what the run carries out and writes, with the placeholders of the
    # section filled in.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    calls, _writes, _active = _install_fake(
        monkeypatch, fixtures, enabled=False, active=set()
    )
    monkeypatch.setattr(values, "MODULE_LOAD_COMMAND", ("my-load", "{module_name}"))
    monkeypatch.setattr(
        values,
        "SWAP_ON_COMMAND",
        (
            "swapon",
            "--discard",
            "--priority",
            "{swap_priority}",
            "{device_path}",
        ),
    )
    monkeypatch.setattr(
        values,
        "UNIT_ALGORITHM_LINE",
        "ExecStart=/sbin/zram-ctl --set {compressor} {algorithm_attribute}",
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert ["my-load", "zram"] in calls
    assert ["swapon", "--discard", "--priority", "1111", "/dev/zram0"] in calls
    written = (tmp_path / "systemd" / "zram.service").read_text(encoding="utf-8")
    assert (
        f"ExecStart=/sbin/zram-ctl --set zstd "
        f"{fixtures['sys_block']}/zram0/comp_algorithm" in written
    )
    assert "ExecStart=/bin/sh -c 'echo zstd" not in written
