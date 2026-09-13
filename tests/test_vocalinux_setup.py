"""Unit tests for the vocalinux_setup task.

All external resources (subprocess, package state, the user manager) are
mocked via monkeypatch; the tests only touch temporary fixtures. The fake
run_command inspects the command shape and answers per command.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
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
AUTOSTART_CONTENT = (
    "[Desktop Entry]\nVersion=1.0\nType=Application\nName=Vocalinux\n"
    "Exec=$appimage --start-minimized\nIcon=vocalinux\n"
    "Comment=Voice dictation for Linux\nTerminal=false\n"
    "StartupNotify=false\nX-GNOME-Autostart-enabled=true\n"
)


def _shipped() -> Any:
    """The vocalinux section of the shared test document."""

    return make_config().vocalinux_setup


def _write_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: Any | None = None,
) -> None:
    """Write the task data templates and point the task at them.

    The fixture file names come from the section, so a renamed template key
    is what the task looks for and the test stays honest; a test that wants
    another name passes its own settings.
    """

    if settings is None:
        settings = _shipped()
    template_dir = tmp_path / "task_data" / "vocalinux_setup"
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / settings.app_config_template_file_name).write_text(
        CONFIG_CONTENT, encoding="utf-8"
    )
    (template_dir / settings.autostart_template_file_name).write_text(
        AUTOSTART_CONTENT, encoding="utf-8"
    )
    (template_dir / settings.echo_desktop_template_file_name).write_text(
        ECHO_CONTENT, encoding="utf-8"
    )


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
) -> Any:
    """Context with the target user home rooted in tmp_path."""

    return make_context(
        task_name="vocalinux_setup",
        install_mode="desktop",
        force_tasks=frozenset({"vocalinux_setup"}) if force else frozenset(),
        repo_root=tmp_path,
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


def _appimage_target(tmp_path: Path, settings: Any | None = None) -> Path:
    """The install path of the AppImage under the fake home."""

    if settings is None:
        settings = _shipped()
    return tmp_path / settings.appimage_dir_relative_path / ASSET


def _user_file(tmp_path: Path, relative_path: str) -> Path:
    """One deployed user file under the fake home."""

    return tmp_path / relative_path


def _seed_installed(tmp_path: Path) -> None:
    """Place the AppImage into the fake user home install directory."""

    target = _appimage_target(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("appimage-bytes", encoding="utf-8")


def _seed_user_files(tmp_path: Path) -> None:
    """Write the matching user files the idempotent rerun expects."""

    settings = _shipped()
    config_path = _user_file(tmp_path, settings.app_config_relative_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(CONFIG_CONTENT, encoding="utf-8")
    autostart_path = _user_file(tmp_path, settings.autostart_relative_path)
    autostart_path.parent.mkdir(parents=True, exist_ok=True)
    autostart_path.write_text(
        task_module._autostart_content(
            AUTOSTART_CONTENT, _appimage_target(tmp_path)
        ),
        encoding="utf-8",
    )
    echo_path = _user_file(tmp_path, settings.echo_desktop_relative_path)
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


def test_release_download_url_comes_from_the_config() -> None:
    # The repository pair and the host template are config values: another
    # pair and another host in the config are the URL the task downloads
    # from, so a mirror needs no code change.
    engine = replace(
        make_config().engine,
        github_release_download_url=(
            "https://mirror.example/{repo}/v{version}/{asset_name}"
        ),
    )
    url = task_module._release_download_url(
        engine, "Owner/App", "1.2.3", "App-1.2.3-x86_64.AppImage"
    )
    assert url == (
        "https://mirror.example/Owner/App/v1.2.3/App-1.2.3-x86_64.AppImage"
    )


def test_user_command_prefix_comes_from_the_config() -> None:
    # The wrapper that runs a command as the target user is a config value:
    # another wrapper in the section is the argv the task builds.
    cfg = replace(
        make_config().vocalinux_setup,
        runuser_command=("sudo", "-u", "{username}", "--"),
    )
    assert task_module._as_user_command(
        cfg, ["kwriteconfig6", "--file", "kglobalshortcutsrc"]
    ) == [
        "sudo",
        "-u",
        cfg.username,
        "--",
        "kwriteconfig6",
        "--file",
        "kglobalshortcutsrc",
    ]


def test_kconfig_calls_come_from_the_config() -> None:
    # The writer and the two selectors are config values: another set of
    # commands is what the task builds for the shortcut file.
    cfg = replace(
        make_config().vocalinux_setup,
        kwriteconfig_command=("my-writer", "--config", "{file_name}"),
        config_group_flag=("--section", "{group}"),
        config_key_flag=("--entry", "{key}"),
    )
    assert task_module._kconfig_command(
        cfg, cfg.kwriteconfig_command, ("services",), "myservice"
    ) == [
        "my-writer",
        "--config",
        cfg.shortcuts_file_name,
        "--section",
        "services",
        "--entry",
        "myservice",
    ]
