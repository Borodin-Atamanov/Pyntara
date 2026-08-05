"""Unit tests for the zram_service task.

All external resources (meminfo, cpuinfo, sysfs, subprocess, filesystem
paths) are mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The unit template is rendered from a
fixture, so the tests never read the repository template.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pyntara.config import (
    AddExtraReposConfig,
    CliToolsConfig,
    Config,
    EngineConfig,
    SwapfileServiceInstallConfig,
)
from pyntara.context import Context
from pyntara.tasks import zram_service

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


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return Context(
        install_mode="server",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset({"zram_service"}) if force else frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=Config(
            engine=EngineConfig(
                task_data_root=tmp_path,
                notice_timeout=7,
                command_timeout_seconds=1800,
                process_check_timeout_seconds=5,
                task_start_delay_seconds=0.5,
            ),
            cli_tools=CliToolsConfig(
                packages=("mc",),
                package_status_timeout_seconds=30,
                package_install_retries=3,
                package_success_threshold_percent=70,
            ),
            add_extra_repos=AddExtraReposConfig(components=("universe",)),
            swapfile_service_install=SwapfileServiceInstallConfig(
                swapfile_path=tmp_path / "swapfile",
                ram_multiplier=2,
                ram_extra_mb=4096,
                disk_fraction=0.5,
            ),
            tasks=(),
        ),
    )


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    ram_kib: int = RAM_KIB,
    cores: int = 2,
    with_cpuinfo: bool = True,
) -> dict[str, Path]:
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
    monkeypatch.setattr(zram_service, "TEMPLATE_PATH", template)
    sys_block = tmp_path / "sys" / "block"
    sys_block.mkdir(parents=True)
    monkeypatch.setattr(zram_service, "SYS_BLOCK_PATH", sys_block)
    control_dir = tmp_path / "sys" / "class" / "zram-control"
    control_dir.mkdir(parents=True)
    hot_add = control_dir / "hot_add"
    hot_remove = control_dir / "hot_remove"
    hot_add.write_text("", encoding="utf-8")
    hot_remove.write_text("", encoding="utf-8")
    monkeypatch.setattr(zram_service, "ZRAM_CONTROL_PATH", hot_add)
    monkeypatch.setattr(zram_service, "SYSTEMD_UNIT_DIR", tmp_path / "systemd")
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
    (device / "comp_algorithm").write_text(
        "lzo lzo-rle [zstd] zstd", encoding="utf-8"
    )
    (device / "disksize").write_text(str(size_bytes), encoding="utf-8")
    (device / "reset").write_text("0", encoding="utf-8")


def _device_count(sys_block: Path) -> int:
    """Number of zram devices present in the sysfs fixture."""

    if not sys_block.is_dir():
        return 0
    return len([path for path in sys_block.iterdir() if path.name.startswith("zram")])


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    fixtures: dict[str, Path],
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
        if path == hot_add:
            count = int(value)
            start = _device_count(sys_block)
            for index in range(start, start + count):
                _create_device(sys_block, index)
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


def _expected_unit(device_count: int, per_device_bytes: int) -> str:
    """The unit file the task must render for the given target."""

    lines = ["ExecStart=/bin/sh -c 'modprobe zram || true'"]
    for index in range(1, device_count):
        lines.append(
            "ExecStart=/bin/sh -c 'echo 1 > /sys/class/zram-control/hot_add'"
        )
    for index in range(device_count):
        lines.append(
            f"ExecStart=/bin/sh -c 'echo zstd > "
            f"/sys/block/zram{index}/comp_algorithm'"
        )
        lines.append(
            f"ExecStart=/bin/sh -c 'echo {per_device_bytes} > "
            f"/sys/block/zram{index}/disksize'"
        )
        lines.append(f"ExecStart=/sbin/mkswap /dev/zram{index}")
        lines.append(f"ExecStart=/sbin/swapon --priority 1111 /dev/zram{index}")
    return UNIT_TEMPLATE.replace("$exec_lines", "\n".join(lines))


def test_calculate_devices_uses_96_percent_and_core_count() -> None:
    # 16 GiB RAM on 2 cores: two devices, each carrying half of 96 percent
    # of RAM rounded down to the 4096-byte zram page size.
    device_count, per_device_bytes = zram_service._calculate_devices(RAM_KIB, 2)
    assert device_count == 2
    total_bytes = RAM_KIB * 1024 * 96 // 100
    assert per_device_bytes * 2 <= total_bytes
    assert per_device_bytes % 4096 == 0
    # Alignment costs less than one page per device.
    assert per_device_bytes * 2 >= total_bytes - 2 * 4096


def test_read_cpu_count_returns_processor_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor : 0\nprocessor : 1\n", encoding="utf-8")
    monkeypatch.setattr(zram_service, "CPUINFO_PATH", cpuinfo)
    assert zram_service._read_cpu_count() == (2, False)


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
    device_count, per_device_bytes = zram_service._calculate_devices(RAM_KIB, 2)
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


def test_creates_devices_and_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Nothing is configured: the task loads the module, creates the missing
    # device, configures both, activates them, renders the unit template
    # and enables the service.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    device_count, per_device_bytes = zram_service._calculate_devices(RAM_KIB, 2)
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
    # modprobe made zram0, hot_add makes zram1.
    assert (fixtures["hot_add"], "1") in writes
    assert active == {"/dev/zram0", "/dev/zram1"}
    unit = tmp_path / "systemd" / "zram.service"
    assert unit.read_text(encoding="utf-8") == _expected_unit(
        device_count, per_device_bytes
    )


def test_fallback_cpu_count_uses_8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Missing cpuinfo: the spec fallback of 8 devices is used and reported.
    fixtures = _install_fixtures(monkeypatch, tmp_path, with_cpuinfo=False)
    calls, writes, _ = _install_fake(
        monkeypatch, fixtures, enabled=False, active=set()
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is True
    assert ["mkswap", "/dev/zram7"] in calls
    # modprobe made zram0, hot_add makes zram1..zram7.
    assert (fixtures["hot_add"], "7") in writes
    assert "8 devices" in (result.message or "")
    captured = capsys.readouterr()
    assert "using fallback 8" in captured.out


def test_removes_extra_devices(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Four devices exist, the target is two: the extras are swapped off,
    # removed and never reconfigured.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    _, per_device_bytes = zram_service._calculate_devices(RAM_KIB, 2)
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
    assert not any(path == fixtures["hot_add"] for path, _ in writes)


def test_force_mode_reconfigures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is already configured, but the task is forced: it swaps
    # the devices off, resets them and configures them again.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    device_count, per_device_bytes = zram_service._calculate_devices(RAM_KIB, 2)
    for index in range(device_count):
        _configure_device(fixtures["sys_block"], index, per_device_bytes)
    active = {f"/dev/zram{index}" for index in range(device_count)}
    calls, _, _ = _install_fake(
        monkeypatch, fixtures, enabled=True, active=set(active)
    )
    result = zram_service.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert result.changed is True
    assert ["swapoff", "/dev/zram0"] in calls
    assert ["mkswap", "/dev/zram0"] in calls
    assert ["systemctl", "enable", "zram.service"] in calls


def test_mkswap_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # mkswap fails on the first device: nothing else may run for it and the
    # task reports the error.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    calls, _, _ = _install_fake(
        monkeypatch,
        fixtures,
        enabled=False,
        active=set(),
        fail=lambda command: command[0] == "mkswap",
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is False
    assert "zram0 setup failed" in (result.error or "")
    assert not any(call == ["mkswap", "/dev/zram1"] for call in calls)


def test_modprobe_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The module cannot load: no device can exist, so the task fails.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    calls, _, _ = _install_fake(
        monkeypatch,
        fixtures,
        enabled=False,
        active=set(),
        fail=lambda command: command[0] == "modprobe",
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is False
    assert "cannot load zram module" in (result.error or "")
    assert not any(call[0] == "mkswap" for call in calls)


def test_missing_template_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit template is missing: the devices are configured but the
    # service cannot be written, so the task reports the error.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    fixtures["template"].unlink()
    calls, _, _ = _install_fake(
        monkeypatch, fixtures, enabled=False, active=set()
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is True
    assert "template" in (result.error or "")
    assert ["mkswap", "/dev/zram0"] in calls


def test_systemctl_enable_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # systemctl enable fails after the devices were configured: the task
    # reports the error and marks the run as changed.
    fixtures = _install_fixtures(monkeypatch, tmp_path)
    calls, _, _ = _install_fake(
        monkeypatch,
        fixtures,
        enabled=False,
        active=set(),
        fail=lambda command: command[:2] == ["systemctl", "enable"],
    )
    result = zram_service.task(_ctx(tmp_path))
    assert result.success is False
    assert result.changed is True
    assert "systemd setup failed" in (result.error or "")
    assert ["mkswap", "/dev/zram0"] in calls
