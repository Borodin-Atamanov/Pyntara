"""Unit tests for the swapfile_service_install task.

All external resources (meminfo, subprocess, disk usage, filesystem paths)
are mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The unit template is rendered from a
fixture, so the tests never read the repository template.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pyntara.config import (
    AddExtraReposConfig,
    CliToolsConfig,
    Config,
    EngineConfig,
    SwapfileServiceInstallConfig,
    ZswapServiceConfig,
)
from pyntara.context import Context
from pyntara.tasks import swapfile_service_install

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

# 16 GiB RAM * 2 + 4096 MiB extra = 36864 MiB target with a large disk.
RAM_KIB = 16 * 1024 * 1024
FREE_BYTES = 100 * 1024**3
TARGET_MB = 16 * 1024 * 2 + 4096


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class _FakeDiskUsage:
    """Minimal stand-in for shutil.disk_usage; only free is read."""

    def __init__(self, free: int) -> None:
        self.total = free
        self.used = 0
        self.free = free


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return Context(
        install_mode="server",
        vault_password=None,
        vault_source=None,
        force_tasks=frozenset({"swapfile_service_install"}) if force else frozenset(),
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
            zswap_service=ZswapServiceConfig(
                enabled=True,
                compressor="zstd",
                max_pool_percent=50,
                accept_threshold_percent=100,
                shrinker_enabled=True,
            ),
            tasks=(),
        ),
    )


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    free_bytes: int = FREE_BYTES,
) -> Path:
    """Point the task at temporary fixtures; return the swapfile path."""

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {RAM_KIB} kB\n", encoding="utf-8")
    monkeypatch.setattr(swapfile_service_install, "MEMINFO_PATH", meminfo)
    template = tmp_path / "task_data" / "swapfile_service_install" / "swapfile.service"
    template.parent.mkdir(parents=True)
    template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(swapfile_service_install, "TEMPLATE_PATH", template)
    monkeypatch.setattr(
        swapfile_service_install, "SYSTEMD_UNIT_DIR", tmp_path / "systemd"
    )
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


def test_force_mode_recreates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_fallocate_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # fallocate fails: nothing else may run and the task reports the error.
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
    assert result.success is False
    assert result.changed is False
    assert "swapfile setup failed" in (result.error or "")


def test_mkswap_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # mkswap fails after the file was created: the task reports the error.
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
    assert result.success is False
    assert "swapfile setup failed" in (result.error or "")


def test_missing_template_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit template is missing: the swapfile is configured but the
    # service cannot be written, so the task reports the error.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    (tmp_path / "task_data" / "swapfile_service_install" / "swapfile.service").unlink()
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    result = swapfile_service_install.task(_ctx(tmp_path))
    assert result.success is False
    assert "template" in (result.error or "")
    assert ["fallocate", "-l", f"{TARGET_MB}M", str(swapfile)] in calls
