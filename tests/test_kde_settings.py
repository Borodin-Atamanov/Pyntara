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

from pyntara.config import KConfigRecord
from pyntara.tasks import kde_settings as task_module


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    kcminputrc: str | None = None,
    virtual_keyboard_enabled: bool = True,
):
    """Context with the target user home rooted in tmp_path.

    kcminputrc, when given, is written into the user config directory so
    the touchpad discovery reads it.
    """

    if kcminputrc is not None:
        config_dir = tmp_path / ".config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "kcminputrc").write_text(kcminputrc, encoding="utf-8")
    return make_context(
        install_mode="desktop",
        force_tasks=frozenset({"kde_settings"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_settings_home_dir=str(tmp_path),
            kde_settings_virtual_keyboard_enabled=virtual_keyboard_enabled,
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
    fail_on_write: bool = False,
):
    """Replace run_command, the session bus and package state.

    currents maps a KConfig key name to its current value, so a key whose
    value matches the target skips the write or apply. bus_pid empty
    disables the desktop session lookup.
    """

    currents = currents or {}
    themes: list[list[str]] = []
    schemes: list[list[str]] = []
    order: list[str] = []
    installs: list[str] = []
    writes: list[list[str]] = []
    reloads: list[list[str]] = []

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
            if inner[0] == "kwriteconfig6":
                if fail_on_write:
                    raise subprocess.CalledProcessError(1, command)
                writes.append(list(command))
                return _FakeProc(0, "")
            if inner[0] == "qdbus6":
                reloads.append(list(command))
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
    return themes, schemes, order, installs, writes, reloads


def test_first_run_applies_both_themes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No current values: the global theme and the color scheme are both
    # applied, the global theme first.
    ctx = _ctx(tmp_path)
    themes, schemes, order, installs, _, _ = _install_fakes(monkeypatch)
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
        "NumLock": "1",
        "InputMethod": "/usr/share/applications/org.kde.plasma.keyboard.desktop",
        "enabledLocales": "en_US,es_MX,ru_RU",
    }
    themes, schemes, order, _, writes, reloads = _install_fakes(
        monkeypatch, currents=currents
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert themes == []
    assert schemes == []
    assert order == []
    assert writes == []
    assert reloads == []


def test_force_applies_even_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force mode applies both themes regardless of the current state.
    ctx = _ctx(tmp_path, force=True)
    currents = {
        "LookAndFeelPackage": "org.kubuntudark.desktop",
        "ColorScheme": "BreezeDark",
    }
    themes, schemes, order, _, _, _ = _install_fakes(
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
    themes, schemes, order, _, _, _ = _install_fakes(
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
    themes, schemes, order, _, _, _ = _install_fakes(
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
    _, _, _, installs, _, _ = _install_fakes(monkeypatch, installed=False)
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
    themes, schemes, _, _, _, _ = _install_fakes(monkeypatch, bus_pid="")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert themes
    assert schemes


TOUCHPAD_RC = """\
[Libinput][2362][597][SYNA3602:00 093A:0255 Touchpad]
ClickMethod=2
DisableEventsOnExternalMouse=true

[Mouse]
cursorSize=72
"""


def test_touchpad_groups_finds_touchpad_sections() -> None:
    # Only the libinput groups whose device name ends with Touchpad match.
    assert task_module._touchpad_groups(TOUCHPAD_RC) == [
        ("Libinput", "2362", "597", "SYNA3602:00 093A:0255 Touchpad")
    ]
    assert task_module._touchpad_groups("[Mouse]\ncursorSize=72\n") == []


def test_numlock_writes_off_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NumLock "off" is written as the value 1, not by deleting the key.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    numlock_writes = [command for command in writes if "NumLock" in command]
    assert numlock_writes
    assert "kcminputrc" in " ".join(numlock_writes[0])
    assert numlock_writes[0][-1] == "1"


def test_numlock_skips_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NumLock already at the "off" value skips the write.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _ = _install_fakes(monkeypatch, currents={"NumLock": "1"})
    task_module.task(ctx)
    assert not [command for command in writes if "NumLock" in command]


def test_touchpad_writes_to_each_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The preferences go to every touchpad group in kcminputrc.
    ctx = _ctx(tmp_path, kcminputrc=TOUCHPAD_RC)
    _, _, _, _, writes, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    click_writes = [command for command in writes if "ClickMethod" in command]
    disable_writes = [
        command for command in writes if "DisableEventsOnExternalMouse" in command
    ]
    assert click_writes
    assert disable_writes
    assert "Libinput" in " ".join(click_writes[0])
    # clickfinger maps to 1, disable on external mouse to false.
    assert click_writes[0][-1] == "1"
    assert disable_writes[0][-1] == "false"


def test_touchpad_missing_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a touchpad group no touchpad writes happen and it is not an
    # error.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert not [command for command in writes if "ClickMethod" in command]


def test_virtual_keyboard_enabled_writes_input_method_and_locales(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The input method goes to kwinrc and the locales to plasmakeyboardrc,
    # then kwin is reloaded.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, reloads = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    input_writes = [
        command
        for command in writes
        if "InputMethod" in command and "kwinrc" in " ".join(command)
    ]
    locale_writes = [command for command in writes if "enabledLocales" in command]
    assert input_writes
    assert locale_writes
    assert "org.kde.plasma.keyboard.desktop" in " ".join(input_writes[0])
    assert "en_US,es_MX,ru_RU" in " ".join(locale_writes[0])
    assert reloads


def test_virtual_keyboard_disabled_removes_input_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Disabled deletes the InputMethod key instead of writing it.
    ctx = _ctx(tmp_path, virtual_keyboard_enabled=False)
    _, _, _, _, writes, reloads = _install_fakes(
        monkeypatch,
        currents={
            "InputMethod": "/usr/share/applications/org.kde.plasma.keyboard.desktop"
        },
    )
    result = task_module.task(ctx)
    assert result.success is True
    delete_writes = [
        command
        for command in writes
        if "InputMethod" in command and "--delete" in command
    ]
    assert delete_writes
    assert reloads


def test_virtual_keyboard_disabled_idempotent_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Disabled with no input method set changes nothing.
    ctx = _ctx(tmp_path, virtual_keyboard_enabled=False)
    _, _, _, _, writes, reloads = _install_fakes(monkeypatch)
    task_module.task(ctx)
    assert not [command for command in writes if "InputMethod" in command]
    assert reloads == []


def _kconfig_ctx(
    tmp_path: Path,
    records: tuple[KConfigRecord, ...],
    *,
    force: bool = False,
):
    """Context whose kconfig list carries the given records."""

    return make_context(
        install_mode="desktop",
        force_tasks=frozenset({"kde_settings"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_settings_home_dir=str(tmp_path),
            kde_settings_kconfig=records,
        ),
    )


FULLY_CONFIGURED = {
    "LookAndFeelPackage": "org.kubuntudark.desktop",
    "ColorScheme": "BreezeDark",
    "NumLock": "1",
    "InputMethod": "/usr/share/applications/org.kde.plasma.keyboard.desktop",
    "enabledLocales": "en_US,es_MX,ru_RU",
}


def test_kconfig_records_write_differing_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Value records with no current value are written; the bool record
    # gets the --type bool flag and the delete record removes a present
    # key.
    records = (
        KConfigRecord(
            "kwinrc", ("TabBox",), "LayoutName", "coverswitch", "string", False
        ),
        KConfigRecord(
            "kdeglobals", ("KDE",), "SingleClick", "true", "bool", False
        ),
        KConfigRecord("kwinrc", ("TabBox",), "StaleKey", "", "string", True),
    )
    ctx = _kconfig_ctx(tmp_path, records)
    _, _, _, _, writes, _ = _install_fakes(
        monkeypatch, currents={"StaleKey": "oldvalue"}
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    layout_writes = [command for command in writes if "LayoutName" in command]
    single_writes = [command for command in writes if "SingleClick" in command]
    delete_writes = [
        command
        for command in writes
        if "StaleKey" in command and "--delete" in command
    ]
    assert layout_writes
    assert layout_writes[0][-1] == "coverswitch"
    assert single_writes
    assert "--type" in single_writes[0] and "bool" in single_writes[0]
    assert delete_writes


def test_kconfig_records_skip_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A value record whose key already matches skips the write; a delete
    # record whose key is absent skips the deletion, so nothing changes.
    records = (
        KConfigRecord(
            "kwinrc", ("TabBox",), "LayoutName", "coverswitch", "string", False
        ),
        KConfigRecord("kwinrc", ("TabBox",), "StaleKey", "", "string", True),
    )
    ctx = _kconfig_ctx(tmp_path, records)
    currents = dict(FULLY_CONFIGURED, LayoutName="coverswitch")
    _, _, _, _, writes, _ = _install_fakes(monkeypatch, currents=currents)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert not [command for command in writes if "LayoutName" in command]
    assert not [command for command in writes if "StaleKey" in command]


def test_kconfig_force_writes_even_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force mode writes the value regardless of the current state.
    records = (
        KConfigRecord(
            "kwinrc", ("TabBox",), "LayoutName", "coverswitch", "string", False
        ),
    )
    ctx = _kconfig_ctx(tmp_path, records, force=True)
    currents = dict(FULLY_CONFIGURED, LayoutName="coverswitch")
    _, _, _, _, writes, _ = _install_fakes(monkeypatch, currents=currents)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert [command for command in writes if "LayoutName" in command]
