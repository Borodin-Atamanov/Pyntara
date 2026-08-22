"""Unit tests for the kde_settings task.

All external resources (subprocess, the session bus, package state) are
mocked via monkeypatch; the tests only touch temporary fixtures. The fake
run_command inspects the command shape and answers per key.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.tasks import kde_settings as task_module


def _ctx(tmp_path: Path, *, force: bool = False):
    """Context with the target user home rooted in tmp_path."""

    return make_context(
        install_mode="desktop",
        force_tasks=frozenset({"kde_settings"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_settings_home_dir=str(tmp_path),
        ),
    )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    currents: dict[str, str] | None = None,
    bus_pid: str = "1763",
    installed: bool = True,
    fail_install: bool = False,
    fail_on_apply: bool = False,
):
    """Replace run_command, the session bus and package state.

    currents maps a kdeglobals key name to its current value, so a key
    whose value matches the target skips the apply. bus_pid empty disables
    the desktop session lookup.
    """

    currents = currents or {}
    themes: list[list[str]] = []
    schemes: list[list[str]] = []
    order: list[str] = []
    installs: list[str] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        if command[:4] == ["runuser", "-u", "i", "--"]:
            inner = command[4:]
            if inner[0] == "kreadconfig6":
                key = inner[inner.index("--key") + 1]
                return _FakeProc(0, currents.get(key, ""))
            if inner[0] == "mkdir":
                return _FakeProc(0, "")
            if inner[0] == "plasma-apply-lookandfeel":
                if fail_on_apply:
                    raise subprocess.CalledProcessError(1, command)
                themes.append(list(command))
                order.append("lookandfeel")
                return _FakeProc(0, "")
            if inner[0] == "plasma-apply-colorscheme":
                if fail_on_apply:
                    raise subprocess.CalledProcessError(1, command)
                schemes.append(list(command))
                order.append("colorscheme")
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
    return themes, schemes, order, installs


def test_first_run_applies_both_themes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No current values: the global theme and the color scheme are both
    # applied, the global theme first.
    ctx = _ctx(tmp_path)
    themes, schemes, order, installs = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(
        "org.kubuntudark.desktop" in command for command in themes
    )
    assert any("BreezeDark" in command for command in schemes)
    assert order == ["lookandfeel", "colorscheme"]
    assert installs == []


def test_skip_when_already_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both values already match: no applies.
    ctx = _ctx(tmp_path)
    currents = {
        "LookAndFeelPackage": "org.kubuntudark.desktop",
        "ColorScheme": "BreezeDark",
    }
    themes, schemes, order, _ = _install_fakes(
        monkeypatch, currents=currents
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert themes == []
    assert schemes == []
    assert order == []


def test_force_applies_even_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force mode applies both themes regardless of the current state.
    ctx = _ctx(tmp_path, force=True)
    currents = {
        "LookAndFeelPackage": "org.kubuntudark.desktop",
        "ColorScheme": "BreezeDark",
    }
    themes, schemes, order, _ = _install_fakes(
        monkeypatch, currents=currents
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert themes
    assert schemes
    assert order == ["lookandfeel", "colorscheme"]


def test_only_color_scheme_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The global theme already matches, only the color scheme differs.
    ctx = _ctx(tmp_path)
    currents = {
        "LookAndFeelPackage": "org.kubuntudark.desktop",
        "ColorScheme": "BreezeLight",
    }
    themes, schemes, order, _ = _install_fakes(
        monkeypatch, currents=currents
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert themes == []
    assert schemes
    assert order == ["colorscheme"]


def test_only_theme_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The color scheme already matches, only the global theme differs.
    ctx = _ctx(tmp_path)
    currents = {
        "LookAndFeelPackage": "org.kde.breeze.desktop",
        "ColorScheme": "BreezeDark",
    }
    themes, schemes, order, _ = _install_fakes(
        monkeypatch, currents=currents
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert themes
    assert schemes == []
    assert order == ["lookandfeel"]


def test_missing_packages_are_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing package is installed before the theme applies.
    ctx = _ctx(tmp_path)
    _, _, _, installs = _install_fakes(monkeypatch, installed=False)
    result = task_module.task(ctx)
    assert result.success is True
    assert installs == ["plasma-workspace", "libkf6config-bin"]


def test_package_install_failure_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed package install is a fatal error result.
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, installed=False, fail_install=True)
    result = task_module.task(ctx)
    assert result.success is False
    assert result.error is not None


def test_apply_failure_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing plasma-apply command is a fatal error result.
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, fail_on_apply=True)
    result = task_module.task(ctx)
    assert result.success is False
    assert result.error is not None


def test_no_desktop_session_still_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a kwin_wayland process the themes are still applied (the
    # config is written) and the run is not an error.
    ctx = _ctx(tmp_path)
    themes, schemes, _, _ = _install_fakes(monkeypatch, bus_pid="")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert themes
    assert schemes
