"""Unit tests for the kde_keyboard_setup task.

All external resources (subprocess, the process environment, package
state) are mocked via monkeypatch; the tests only touch temporary
fixtures (docs/guides/developer-guide.md). The fake run_command inspects
the command shape and answers per key.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.tasks import kde_keyboard_setup as task_module

SAMPLE_APPLETSRC = """\
[Containments][2]
plugin=org.kde.plasma.panel

[Containments][2][Applets][7]
plugin=org.kde.plasma.systemtray

[Containments][2][Applets][7][Applets][19]
plugin=org.kde.plasma.keyboardlayout

[Containments][2][Applets][7][Applets][19][Configuration][General]
displayStyle=Flag
"""


def _ctx(tmp_path: Path, *, force: bool = False, appletsrc: str = SAMPLE_APPLETSRC):
    """Context with the target config directory rooted in tmp_path."""

    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "plasma-org.kde.plasma.desktop-appletsrc").write_text(
        appletsrc, encoding="utf-8"
    )
    return make_context(
        install_mode="desktop",
        force_tasks=frozenset({"kde_keyboard_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_keyboard_setup_username="i",
            kde_keyboard_setup_home_dir=str(tmp_path),
            kde_keyboard_setup_config_dir=str(config_dir),
        ),
    )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    currents: dict[str, str] | None = None,
    bus_pid: str = "1763",
    installed: bool = True,
    fail_install: bool = False,
    fail_on_write: bool = False,
):
    """Replace run_command and package state; return captured command lists.

    currents maps a KConfig key name to its current value, so a key whose
    value matches the target skips the write. bus_pid empty disables the
    desktop session lookup.
    """

    currents = currents or {}
    writes: list[list[str]] = []
    reloads: list[list[str]] = []
    restarts: list[list[str]] = []
    installs: list[str] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        if command[:4] == ["runuser", "-u", "i", "--"]:
            inner = command[4:]
            if inner[0] == "kreadconfig6":
                key = inner[inner.index("--key") + 1]
                return _FakeProc(0, currents.get(key, ""))
            if inner[0] == "kwriteconfig6":
                if fail_on_write:
                    raise subprocess.CalledProcessError(1, command)
                writes.append(list(command))
                return _FakeProc(0, "")
            if inner[0] == "mkdir":
                return _FakeProc(0, "")
            if inner[0] == "qdbus6":
                reloads.append(list(command))
                return _FakeProc(0, "")
        if command[0] == "pgrep":
            return _FakeProc(0, f"{bus_pid}\n" if bus_pid else "")
        if command[0] == "systemctl":
            restarts.append(list(command))
            return _FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    def fake_installed(package: str, timeout: float) -> bool:
        return installed

    def fake_install(package: str, timeout: float) -> tuple[bool, str]:
        if fail_install:
            return False, "cannot install"
        installs.append(package)
        return True, ""

    monkeypatch.setattr(task_module, "run_command", fake_run)
    monkeypatch.setattr(task_module, "package_is_installed", fake_installed)
    monkeypatch.setattr(task_module, "install_package_once", fake_install)
    monkeypatch.setattr(
        task_module,
        "session_bus_address",
        (
            lambda username, timeout: "unix:path=/run/user/1000/bus"
            if bus_pid
            else None
        ),
    )
    return writes, reloads, restarts, installs


def test_first_run_writes_and_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No current values: every kxkbrc key and the display style are
    # written, then kwin is reloaded and the panel restarted.
    ctx = _ctx(tmp_path)
    writes, reloads, restarts, installs = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    layout_writes = [
        command for command in writes if "--file" in command and "kxkbrc" in command
    ]
    assert any("LayoutList" in command and "us,ru,es" in command for command in layout_writes)
    assert any("Options" in command and "grp:caps_select" in command for command in layout_writes)
    assert any("Use" in command and "true" in command for command in layout_writes)
    display_writes = [
        command for command in writes if "appletsrc" in " ".join(command)
    ]
    assert any("displayStyle" in command and "Flag" in command for command in display_writes)
    assert reloads
    assert restarts
    assert installs == []


def test_skip_when_already_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every value already matches: no writes, no reloads, no panel restart.
    ctx = _ctx(tmp_path)
    currents = {
        "LayoutList": "us,ru,es",
        "Options": "grp:caps_select",
        "Use": "true",
        "displayStyle": "Flag",
    }
    writes, reloads, restarts, _ = _install_fakes(
        monkeypatch, currents=currents
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert writes == []
    assert reloads == []
    assert restarts == []


def test_force_rewrites_even_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force mode rewrites the values and reloads regardless of the current
    # state.
    ctx = _ctx(tmp_path, force=True)
    currents = {
        "LayoutList": "us,ru,es",
        "Options": "grp:caps_select",
        "Use": "true",
        "displayStyle": "Flag",
    }
    writes, reloads, restarts, _ = _install_fakes(
        monkeypatch, currents=currents
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert writes
    assert reloads
    assert restarts


def test_missing_packages_are_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing package is installed before the config writes.
    ctx = _ctx(tmp_path)
    _, _, _, installs = _install_fakes(monkeypatch, installed=False)
    result = task_module.task(ctx)
    assert result.success is True
    assert installs == ["libkf6config-bin", "qdbus-qt6"]


def test_package_install_failure_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed package install is a fatal error result.
    ctx = _ctx(tmp_path)
    _, _, _, _ = _install_fakes(
        monkeypatch, installed=False, fail_install=True
    )
    result = task_module.task(ctx)
    assert result.success is False
    assert result.error is not None


def test_no_desktop_session_skips_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a kwin_wayland process the reload is skipped, not fatal.
    ctx = _ctx(tmp_path)
    writes, reloads, _, _ = _install_fakes(monkeypatch, bus_pid="")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert writes
    assert reloads == []


def test_applet_missing_leaves_indicator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without the keyboard layout applet the indicator is left as is, no
    # panel restart.
    ctx = _ctx(tmp_path, appletsrc="[Containments][2]\nplugin=org.kde.plasma.panel\n")
    writes, _, restarts, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    display_writes = [
        command for command in writes if "appletsrc" in " ".join(command)
    ]
    assert display_writes == []
    assert restarts == []


def test_write_failure_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing kwriteconfig6 is a fatal error result.
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, fail_on_write=True)
    result = task_module.task(ctx)
    assert result.success is False
    assert result.error is not None


def test_keyboard_layout_config_group_finds_nested_applet() -> None:
    # The Configuration/General group of the keyboard layout applet is
    # derived from the nested group that declares the plugin.
    group = task_module._keyboard_layout_config_group(
        SAMPLE_APPLETSRC, "org.kde.plasma.keyboardlayout"
    )
    assert group == (
        "Containments",
        "2",
        "Applets",
        "7",
        "Applets",
        "19",
        "Configuration",
        "General",
    )


def test_keyboard_layout_config_group_missing() -> None:
    # A document without the applet returns None.
    assert (
        task_module._keyboard_layout_config_group(
            "[Containments][2]\nplugin=org.kde.plasma.panel\n",
            "org.kde.plasma.keyboardlayout",
        )
        is None
    )
