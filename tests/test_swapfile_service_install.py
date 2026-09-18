"""Unit tests for the swapfile_service_install task.

All external resources (meminfo, subprocess, disk usage, filesystem paths)
are mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The unit template is rendered from a
fixture, so the tests never read the repository template. The swapfile path,
the template name and the /proc/meminfo line name are values, so one autouse
fixture points them at the temporary tree of the test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara.context import Context
from pyntara.tasks import swapfile_service_install
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import swapfile_service_install as values

UNIT_TEMPLATE = """\
[Unit]
Description=Activate swap file
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/swapon $swapfile_path
ExecStop=/sbin/swapoff $swapfile_path

[Install]
WantedBy=multi-user.target
"""

# 16 GiB RAM * the shipped multiplier + the shipped extra mebibytes, capped by
# a large disk: the target the task computes with the values of the section.
# The engine byte factor comes from the engine values module, which the test
# patches where it needs another one.
RAM_KIB = 16 * 1024 * 1024
FREE_BYTES = 100 * 1024**3
TARGET_MB = (
    int(RAM_KIB // engine_values.BYTES_PER_KIB * values.RAM_MULTIPLIER)
    + values.RAM_EXTRA_MB
)


class _FakeDiskUsage:
    """Minimal stand-in for shutil.disk_usage; only free is read."""

    def __init__(self, free: int) -> None:
        self.total = free
        self.used = 0
        self.free = free


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own swapfile and template directory.

    The swapfile path and the template name are values of the section and the
    meminfo line name comes from the shared module; the fixture points them at
    the temporary directory of the test, so no test touches /swapfile or the
    real /proc/meminfo.
    """

    monkeypatch.setattr(values, "SWAPFILE_PATH", tmp_path / "swapfile")
    monkeypatch.setattr(values, "UNIT_TEMPLATE_FILE_NAME", "swapfile.service")
    monkeypatch.setattr(common_values, "MEMINFO_TOTAL_KEY", "MemTotal:")
    monkeypatch.setattr(engine_values, "SYSTEMD_UNIT_DIR", tmp_path / "systemd")


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    """Context with a small safe config; the real file is never touched.

    The declared values are read from the values modules, which the autouse
    fixture points at the temporary tree: the section values and the unit
    directory of the engine, because the task writes the unit file there and
    reads the template from there.
    """

    return make_context(
        task_name="swapfile_service_install",
        install_mode="server",
        force_tasks=(frozenset({"swapfile_service_install"}) if force else frozenset()),
        task_data_root=tmp_path,
        repo_root=tmp_path,
        skip_apt_update=True,
    )


def test_the_meminfo_line_name_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The name of the /proc/meminfo line that carries the installed RAM is
    # a shared value: another name is the line the task reads.
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("Total-RAM:       8192 kB\n", encoding="utf-8")
    monkeypatch.setattr(swapfile_service_install, "MEMINFO_PATH", meminfo)
    monkeypatch.setattr(common_values, "MEMINFO_TOTAL_KEY", "Total-RAM:")
    assert swapfile_service_install._read_ram_kib() == 8192


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    free_bytes: int = FREE_BYTES,
    unit_template_file_name: str = "swapfile.service",
) -> Path:
    """Point the task at temporary fixtures; return the swapfile path."""

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        f"{common_values.MEMINFO_TOTAL_KEY}       {RAM_KIB} kB\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(swapfile_service_install, "MEMINFO_PATH", meminfo)
    monkeypatch.setattr(values, "UNIT_TEMPLATE_FILE_NAME", unit_template_file_name)
    template = (
        tmp_path / "task_data" / "swapfile_service_install" / unit_template_file_name
    )
    template.parent.mkdir(parents=True)
    template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(
        "pyntara.tasks.swapfile_service_install.shutil.disk_usage",
        lambda path: _FakeDiskUsage(free=free_bytes),
    )
    return tmp_path / "swapfile"


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    swapfile_path: Path,
    *,
    active: bool,
    enabled: bool,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    swapon --show answers from the active flag, systemctl is-enabled from
    the enabled flag, every other command succeeds and is recorded.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "swapon" and command[1] == "--show":
            if active:
                return _FakeProc(0, f"{swapfile_path}\n")
            return _FakeProc(0, "")
        if command[0] == "systemctl" and command[1] == "is-enabled":
            if enabled:
                return _FakeProc(0, "enabled\n")
            return _FakeProc(1, "disabled")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_already_configured_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The swapfile exists at the computed size, is active and the service is
    # enabled: the task skips and runs only the status queries.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    with swapfile.open("wb") as handle:
        handle.truncate(TARGET_MB * 1024 * 1024)
    calls = _install_fake(monkeypatch, swapfile, active=True, enabled=True)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert all(call[0] in ("swapon", "systemctl") for call in calls)


def test_creates_swapfile_and_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Nothing is configured: the task creates the swapfile at the computed
    # size, activates it, renders the unit template and enables the service.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert ["fallocate", "-l", f"{TARGET_MB}M", str(swapfile)] in calls
    assert ["chmod", "600", str(swapfile)] in calls
    assert ["mkswap", str(swapfile)] in calls
    assert ["swapon", str(swapfile)] in calls
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "swapfile.service"] in calls
    unit = tmp_path / "systemd" / "swapfile.service"
    expected = UNIT_TEMPLATE.replace("$swapfile_path", str(swapfile))
    assert unit.read_text(encoding="utf-8") == expected


def test_target_size_follows_the_engine_byte_factor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The declared byte factor is the factor the task counts RAM and free
    # disk with, so the fixture points it at another number for this test.
    monkeypatch.setattr(engine_values, "BYTES_PER_KIB", 1000)
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    ctx = _ctx(tmp_path)
    result = swapfile_service_install.task(ctx)
    assert result.success is True
    ram_based = int(RAM_KIB // 1000 * values.RAM_MULTIPLIER) + values.RAM_EXTRA_MB
    disk_based = int(FREE_BYTES // 1000 // 1000 * values.DISK_FRACTION)
    expected_mb = min(ram_based, disk_based)
    assert ["fallocate", "-l", f"{expected_mb}M", str(swapfile)] in calls


def test_activates_existing_file_when_service_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The swapfile already exists at the computed size and is active, but
    # the service is disabled: the task enables it without recreating the
    # file.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    with swapfile.open("wb") as handle:
        handle.truncate(TARGET_MB * 1024 * 1024)
    calls = _install_fake(monkeypatch, swapfile, active=True, enabled=False)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] in ("swapoff", "fallocate") for call in calls)
    assert ["systemctl", "enable", "swapfile.service"] in calls


def test_force_mode_recreates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Everything is already configured, but the task is forced: it swaps the
    # file off, recreates it and re-enables the service.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    with swapfile.open("wb") as handle:
        handle.truncate(TARGET_MB * 1024 * 1024)
    calls = _install_fake(monkeypatch, swapfile, active=True, enabled=True)
    result = swapfile_service_install.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert result.changed is True
    assert ["swapoff", str(swapfile)] in calls
    assert ["fallocate", "-l", f"{TARGET_MB}M", str(swapfile)] in calls


def test_disk_fraction_limits_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Only 8 GiB are free: the disk cap of 8 GiB * 0.5 = 4096 MiB wins over
    # the RAM-based 36864 MiB, so the swapfile is created at 4096 MiB.
    swapfile = _install_fixtures(monkeypatch, tmp_path, free_bytes=8 * 1024**3)
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert ["fallocate", "-l", "4096M", str(swapfile)] in calls
    assert "4096M" in (result.message or "")


def test_fallocate_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # fallocate fails: the step is reported and the boot service is still
    # installed, because the unit creates the file on its own.
    _install_fixtures(monkeypatch, tmp_path)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "fallocate":
            raise subprocess.CalledProcessError(1, command)
        if command[0] == "swapon" and command[1] == "--show":
            return _FakeProc(0, "")
        if command[0] == "systemctl" and command[1] == "is-enabled":
            return _FakeProc(1, "disabled")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert any("swapfile setup failed" in warning for warning in result.warnings)
    assert result.changed is True
    assert (tmp_path / "systemd" / "swapfile.service").is_file()


def test_mkswap_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # mkswap fails after the file was created: the task reports the reason
    # and continues with the boot service.
    _install_fixtures(monkeypatch, tmp_path)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "mkswap":
            raise subprocess.CalledProcessError(1, command)
        if command[0] == "swapon" and command[1] == "--show":
            return _FakeProc(0, "")
        if command[0] == "systemctl" and command[1] == "is-enabled":
            return _FakeProc(1, "disabled")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert any("swapfile setup failed" in w for w in result.warnings)


def test_missing_template_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit template is missing: the swapfile is configured, the unit
    # step is skipped alone and the task completes.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    (tmp_path / "task_data" / "swapfile_service_install" / "swapfile.service").unlink()
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert any("template" in warning for warning in result.warnings)
    assert ["fallocate", "-l", f"{TARGET_MB}M", str(swapfile)] in calls


def test_commands_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every command of the task is a value: another allocation call, another
    # mode call and another enable call in the values module are the argv the
    # run carries out, with the placeholders of the section filled in.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    monkeypatch.setattr(
        values,
        "CREATE_COMMAND",
        ("my-allocate", "--length", "{size_mb}M", "{swapfile_path}"),
    )
    monkeypatch.setattr(
        values, "CHMOD_COMMAND", ("my-chmod", "{file_mode}", "{swapfile_path}")
    )
    monkeypatch.setattr(
        values, "SYSTEMCTL_ENABLE_COMMAND", ("my-enable", "{service_unit_name}")
    )
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    assert ["my-allocate", "--length", f"{TARGET_MB}M", str(swapfile)] in calls
    assert ["my-chmod", "600", str(swapfile)] in calls
    assert ["my-enable", "swapfile.service"] in calls


def test_unit_template_name_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The template of the unit is named by the values module: the run renders
    # the file the value names and leaves the other template of the directory
    # unread.
    swapfile = _install_fixtures(
        monkeypatch, tmp_path, unit_template_file_name="other.service"
    )
    (
        tmp_path / "task_data" / "swapfile_service_install" / "swapfile.service"
    ).write_text("[Unit]\nDescription=wrong\n", encoding="utf-8")
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is True
    written = (tmp_path / "systemd" / "swapfile.service").read_text(encoding="utf-8")
    assert "wrong" not in written
    assert str(swapfile) in written
    assert ["systemctl", "enable", "swapfile.service"] in calls
