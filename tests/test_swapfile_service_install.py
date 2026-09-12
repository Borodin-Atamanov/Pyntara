"""Unit tests for the swapfile_service_install task.

All external resources (meminfo, subprocess, disk usage, filesystem paths)
are mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The unit template is rendered from a
fixture, so the tests never read the repository template.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

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


class _FakeDiskUsage:
    """Minimal stand-in for shutil.disk_usage; only free is read."""

    def __init__(self, free: int) -> None:
        self.total = free
        self.used = 0
        self.free = free


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    unit_template_file_name: str = "swapfile.service",
    create_command: tuple[str, ...] = (
        "fallocate",
        "-l",
        "{size_mb}M",
        "{swapfile_path}",
    ),
    chmod_command: tuple[str, ...] = ("chmod", "{file_mode}", "{swapfile_path}"),
    enable_command: tuple[str, ...] = (
        "systemctl",
        "enable",
        "{service_unit_name}",
    ),
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        task_name="swapfile_service_install",
        install_mode="server",
        force_tasks=(
            frozenset({"swapfile_service_install"}) if force else frozenset()
        ),
        task_data_root=tmp_path,
        repo_root=tmp_path,
        skip_apt_update=True,
        config=make_config(
            task_data_root=tmp_path,
            systemd_unit_dir=tmp_path / "systemd",
            cli_tools_packages=("mc",),
            add_extra_repos_components=("universe",),
            swapfile_path=tmp_path / "swapfile",
            swapfile_unit_template_file_name=unit_template_file_name,
            swapfile_create_command=create_command,
            swapfile_chmod_command=chmod_command,
            swapfile_systemctl_enable_command=enable_command,
        ),
    )


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    free_bytes: int = FREE_BYTES,
    unit_template_file_name: str = "swapfile.service",
) -> Path:
    """Point the task at temporary fixtures; return the swapfile path."""

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {RAM_KIB} kB\n", encoding="utf-8")
    monkeypatch.setattr(swapfile_service_install, "MEMINFO_PATH", meminfo)
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
    # Another byte factor in the [engine] table is the factor the task
    # counts RAM and free disk with, so it is not a value of the module.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    ctx = _ctx(tmp_path)
    config = ctx.config
    ctx = replace(
        ctx,
        config=replace(config, engine=replace(config.engine, bytes_per_kib=1000)),
    )
    result = swapfile_service_install.task(ctx)
    assert result.success is True
    swap = config.swapfile_service_install
    ram_based = int(RAM_KIB // 1000 * swap.ram_multiplier) + swap.ram_extra_mb
    disk_based = int(FREE_BYTES // 1000 // 1000 * swap.disk_fraction)
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


def test_commands_come_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every command of the task is a config value: another allocation call,
    # another mode call and another enable call in the config are the argv
    # the run carries out, with the placeholders of the section filled in.
    swapfile = _install_fixtures(monkeypatch, tmp_path)
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    result = swapfile_service_install.task(
        _ctx(
            tmp_path,
            create_command=("my-allocate", "--length", "{size_mb}M", "{swapfile_path}"),
            chmod_command=("my-chmod", "{file_mode}", "{swapfile_path}"),
            enable_command=("my-enable", "{service_unit_name}"),
        )
    )
    assert result.success is True
    assert ["my-allocate", "--length", f"{TARGET_MB}M", str(swapfile)] in calls
    assert ["my-chmod", "600", str(swapfile)] in calls
    assert ["my-enable", "swapfile.service"] in calls


def test_unit_template_name_comes_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The template of the unit is named by the config: the run renders the
    # file the config names and leaves the other template of the directory
    # unread.
    swapfile = _install_fixtures(
        monkeypatch, tmp_path, unit_template_file_name="other.service"
    )
    (tmp_path / "task_data" / "swapfile_service_install" / "swapfile.service").write_text(
        "[Unit]\nDescription=wrong\n", encoding="utf-8"
    )
    calls = _install_fake(monkeypatch, swapfile, active=False, enabled=False)
    result = swapfile_service_install.task(
        _ctx(tmp_path, unit_template_file_name="other.service")
    )
    assert result.success is True
    written = (tmp_path / "systemd" / "swapfile.service").read_text(encoding="utf-8")
    assert "wrong" not in written
    assert str(swapfile) in written
    assert ["systemctl", "enable", "swapfile.service"] in calls
