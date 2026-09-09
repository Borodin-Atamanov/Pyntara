"""Unit tests for the vocalinux_setup task.

All external resources (subprocess, package state, the user manager) are
mocked via monkeypatch; the tests only touch temporary fixtures. The fake
run_command inspects the command shape and answers per command.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.tasks import vocalinux_setup as task_module

ASSET = "Vocalinux-0.16.2-x86_64.AppImage"
CONFIG_CONTENT = '{"speech_recognition": {"engine": "whisper_cpp"}}\n'
ECHO_CONTENT = (
    "[Desktop Entry]\nExec=echo '' > /dev/null\nName=nada\n"
    "NoDisplay=true\nType=Application\n"
    "X-KDE-GlobalAccel-CommandShortcut=true\n"
)


def _write_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the task data templates and point the task at them."""

    template_dir = tmp_path / "task_data" / "vocalinux_setup"
    template_dir.mkdir(parents=True, exist_ok=True)
    config_path = template_dir / "config.json"
    config_path.write_text(CONFIG_CONTENT, encoding="utf-8")
    echo_path = template_dir / "net.local.echo.desktop"
    echo_path.write_text(ECHO_CONTENT, encoding="utf-8")
    monkeypatch.setattr(task_module, "CONFIG_TEMPLATE", config_path)
    monkeypatch.setattr(task_module, "ECHO_DESKTOP_TEMPLATE", echo_path)


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
) -> Any:
    """Context with the target user home rooted in tmp_path."""

    return make_context(
        install_mode="desktop",
        force_tasks=frozenset({"vocalinux_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            vocalinux_home_dir=str(tmp_path),
            vocalinux_download_dir=tmp_path / "cache",
        ),
    )


class Fakes:
    """Recording of the calls the fakes answered."""

    def __init__(self) -> None:
        self.kwrites: list[list[str]] = []
        self.curls: list[list[str]] = []
        self.usermods: list[list[str]] = []
        self.service_enables: list[list[str]] = []


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current_shortcut: str = "",
    installed: bool = False,
    fail_install: bool = False,
    group_present: bool = False,
    fail_group: bool = False,
    service_active: bool = False,
    fail_service: bool = False,
    fail_download: bool = False,
) -> Fakes:
    """Replace run_command, package state and the user manager.

    current_shortcut is the value kreadconfig6 reports for the Meta+S key,
    so a matching value skips the shortcut write. The AppImage download is
    simulated by creating the --output file the task then renames into the
    cache and copies into the user home.
    """

    fakes = Fakes()

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        if command[:4] == ["runuser", "-u", "i", "--"]:
            inner = command[4:]
            if inner[0] == "mkdir":
                return _FakeProc(0, "")
            if inner[0] == "kreadconfig6":
                return _FakeProc(0, current_shortcut)
            if inner[0] == "kwriteconfig6":
                fakes.kwrites.append(list(command))
                return _FakeProc(0, "")
        if command[0] in ("chown", "chmod"):
            return _FakeProc(0, "")
        if command[0] == "id" and command[1:3] == ["-nG", "i"]:
            memberships = "i input" if group_present else "i"
            return _FakeProc(0, memberships)
        if command[0] == "usermod":
            if fail_group:
                raise subprocess.CalledProcessError(1, command)
            fakes.usermods.append(list(command))
            return _FakeProc(0, "")
        if command[0] == "systemctl":
            if command[1:] == [
                "--user",
                "--machine",
                "i@.host",
                "is-active",
                "ydotool.service",
            ]:
                if service_active:
                    return _FakeProc(0, "active")
                return _FakeProc(1, "inactive")
            if command[1:] == [
                "--user",
                "--machine",
                "i@.host",
                "enable",
                "--now",
                "ydotool.service",
            ]:
                if fail_service:
                    raise subprocess.CalledProcessError(1, command)
                fakes.service_enables.append(list(command))
                return _FakeProc(0, "")
        if command[0] == "curl":
            output_index = command.index("--output")
            output_path = Path(command[output_index + 1])
            fakes.curls.append(list(command))
            if fail_download:
                raise subprocess.CalledProcessError(22, command)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("appimage-bytes", encoding="utf-8")
            return _FakeProc(0, "Downloaded 13 bytes")
        raise AssertionError(f"unexpected command: {command}")

    def fake_installed(package: str, timeout: float) -> bool:
        return installed

    def fake_install(
        packages: list[str],
        *,
        install_timeout: float,
        update_timeout: float,
        retries: int,
        skip_update: bool,
    ) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        if fail_install:
            return [], [(package, "cannot install") for package in packages], []
        return packages, [], []

    monkeypatch.setattr(task_module, "run_command", fake_run)
    monkeypatch.setattr(task_module, "package_is_installed", fake_installed)
    monkeypatch.setattr(task_module, "install_packages", fake_install)
    monkeypatch.setattr(task_module, "dpkg_architecture", lambda timeout: "amd64")
    return fakes


def _appimage_target(tmp_path: Path) -> Path:
    """The install path of the AppImage under the fake home."""

    return tmp_path / ".local" / "share" / "vocalinux" / "appimage" / ASSET


def _seed_installed(tmp_path: Path) -> None:
    """Place the AppImage into the fake user home install directory."""

    target = _appimage_target(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("appimage-bytes", encoding="utf-8")


def _seed_user_files(tmp_path: Path) -> None:
    """Write the matching user files the idempotent rerun expects."""

    config_path = tmp_path / ".config" / "vocalinux" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(CONFIG_CONTENT, encoding="utf-8")
    autostart_path = tmp_path / ".config" / "autostart" / "vocalinux.desktop"
    autostart_path.parent.mkdir(parents=True, exist_ok=True)
    autostart_path.write_text(
        task_module._autostart_content(_appimage_target(tmp_path)),
        encoding="utf-8",
    )
    echo_path = tmp_path / ".local" / "share" / "applications" / "net.local.echo.desktop"
    echo_path.parent.mkdir(parents=True, exist_ok=True)
    echo_path.write_text(ECHO_CONTENT, encoding="utf-8")


def test_fresh_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine without Vocalinux gets the app and all the pieces."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    fakes = _install_fakes(monkeypatch)

    result = task_module.task(ctx)

    assert result.success is True
    assert result.changed is True
    assert result.warnings == ()
    target = _appimage_target(tmp_path)
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "appimage-bytes"
    assert (tmp_path / "cache" / ASSET).is_file()
    assert (tmp_path / ".config" / "vocalinux" / "config.json").read_text(
        encoding="utf-8"
    ) == CONFIG_CONTENT
    autostart = (tmp_path / ".config" / "autostart" / "vocalinux.desktop").read_text(
        encoding="utf-8"
    )
    assert f"Exec={target} --start-minimized" in autostart
    assert (
        tmp_path / ".local" / "share" / "applications" / "net.local.echo.desktop"
    ).read_text(encoding="utf-8") == ECHO_CONTENT
    assert fakes.curls and fakes.curls[0][0] == "curl"
    assert fakes.usermods and fakes.usermods[0][:3] == ["usermod", "-aG", "input"]
    assert fakes.service_enables
    assert fakes.kwrites
    assert fakes.kwrites[0][-2:] == ["_launch", "Meta+S"]


def test_idempotent_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fully configured machine changes nothing on a rerun."""

    _write_templates(tmp_path, monkeypatch)
    _seed_installed(tmp_path)
    _seed_user_files(tmp_path)
    ctx = _ctx(tmp_path)
    fakes = _install_fakes(
        monkeypatch,
        current_shortcut="Meta+S",
        installed=True,
        group_present=True,
        service_active=True,
    )

    result = task_module.task(ctx)

    assert result.success is True
    assert result.changed is False
    assert result.warnings == ()
    assert fakes.curls == []
    assert fakes.usermods == []
    assert fakes.service_enables == []
    assert fakes.kwrites == []


def test_package_install_failure_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package that cannot be installed is an error result."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, installed=False, fail_install=True)

    result = task_module.task(ctx)

    assert result.success is False
    assert result.error is not None


def test_download_failure_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed AppImage download is an error result."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, installed=True, fail_download=True)

    result = task_module.task(ctx)

    assert result.success is False
    assert "cannot download" in (result.error or "")


def test_recoverable_steps_report_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed group or service step is a warning, not an error."""

    _write_templates(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    _install_fakes(
        monkeypatch,
        installed=True,
        group_present=False,
        fail_group=True,
        service_active=False,
        fail_service=True,
    )

    result = task_module.task(ctx)

    assert result.success is True
    assert result.changed is True
    assert len(result.warnings) == 2
    assert any("input" in warning for warning in result.warnings)
    assert any("ydotool.service" in warning for warning in result.warnings)
    assert (tmp_path / ".config" / "vocalinux" / "config.json").is_file()


def test_force_rewrites_matching_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force mode rewrites files and reinstalls even when already set."""

    _write_templates(tmp_path, monkeypatch)
    _seed_installed(tmp_path)
    cache = tmp_path / "cache" / ASSET
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("appimage-bytes", encoding="utf-8")
    _seed_user_files(tmp_path)
    ctx = _ctx(tmp_path, force=True)
    fakes = _install_fakes(
        monkeypatch,
        current_shortcut="Meta+S",
        installed=True,
        group_present=True,
        service_active=True,
    )

    result = task_module.task(ctx)

    assert result.success is True
    assert result.changed is True
    assert fakes.curls == []
    assert fakes.kwrites  # force re-registers the shortcut


def test_missing_config_template_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing config template stops the task as an error."""

    _write_templates(tmp_path, monkeypatch)
    (tmp_path / "task_data" / "vocalinux_setup" / "config.json").unlink()
    _seed_installed(tmp_path)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, installed=True, group_present=True, service_active=True)

    result = task_module.task(ctx)

    assert result.success is False
    assert "config template" in (result.error or "")
