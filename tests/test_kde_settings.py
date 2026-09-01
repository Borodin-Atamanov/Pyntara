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
    cursorthemes: list[list[str]] = []
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
            if inner[0] == "plasma-apply-cursortheme":
                if fail_on_apply:
                    raise subprocess.CalledProcessError(1, command)
                cursorthemes.append(list(command))
                return _FakeProc(0, "")
            if inner[0] == "kwriteconfig6":
                if fail_on_write:
                    raise subprocess.CalledProcessError(1, command)
                writes.append(list(command))
                return _FakeProc(0, "")
            if inner[0] == "qdbus6":
                reloads.append(list(command))
                return _FakeProc(0, "")
        if command[0] in ("chown", "chmod"):
            return _FakeProc(0, "")
        if command[0] == "kreadconfig6":
            key = command[command.index("--key") + 1]
            return _FakeProc(0, currents.get(key, ""))
        if command[0] == "kwriteconfig6":
            writes.append(list(command))
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
    # The system theme directory never exists, so the theme cursor
    # overrides skip the copy in the general task tests; the override
    # tests point it at their own fixtures.
    monkeypatch.setattr(
        task_module, "SYSTEM_LOOK_AND_FEEL_DIR", Path("/nonexistent/look-and-feel")
    )
    return themes, schemes, order, installs, writes, reloads, cursorthemes


def test_first_run_applies_both_themes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No current values: the global theme and the color scheme are both
    # applied, the global theme first.
    ctx = _ctx(tmp_path)
    themes, schemes, order, installs, _, _, _ = _install_fakes(
        monkeypatch
    )
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
        "User": "i",
        "Session": "plasma",
        "Current": "kubuntu",
        "CursorSize": "30",
        "CursorTheme": "breeze_cursors",
        "cursorTheme": "Oxygen_Yellow",
        "Font": "Noto Sans,20",
        "window-grow-shrinkEnabled": "true",
        "window-restore-trackerEnabled": "true",
    }
    _preconfigure_user_files(tmp_path, ctx.config.kde_settings)
    themes, schemes, order, _, writes, reloads, _ = _install_fakes(
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
    themes, schemes, order, _, _, _, _ = _install_fakes(
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
    themes, schemes, order, _, _, _, _ = _install_fakes(
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
    themes, schemes, order, _, _, _, _ = _install_fakes(
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
    _, _, _, installs, _, _, _ = _install_fakes(monkeypatch, installed=False)
    result = task_module.task(ctx)
    assert result.success is True
    assert installs == [
        "plasma-workspace",
        "libkf6config-bin",
        "kubuntu-settings-desktop",
        "python3-dbus",
    ]


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
    themes, schemes, _, _, _, _, _ = _install_fakes(monkeypatch, bus_pid="")
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
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
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
    _, _, _, _, writes, _, _ = _install_fakes(
        monkeypatch, currents={"NumLock": "1"}
    )
    task_module.task(ctx)
    assert not [command for command in writes if "NumLock" in command]


def test_touchpad_writes_to_each_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The preferences go to every touchpad group in kcminputrc.
    ctx = _ctx(tmp_path, kcminputrc=TOUCHPAD_RC)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
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
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert not [command for command in writes if "ClickMethod" in command]


def test_virtual_keyboard_enabled_writes_input_method_and_locales(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The input method goes to kwinrc and the locales to plasmakeyboardrc,
    # then kwin is reloaded.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, reloads, _ = _install_fakes(monkeypatch)
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
    _, _, _, _, writes, reloads, _ = _install_fakes(
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
    currents = {
        "window-grow-shrinkEnabled": "true",
        "window-restore-trackerEnabled": "true",
    }
    _preconfigure_user_files(tmp_path, ctx.config.kde_settings)
    _, _, _, _, writes, reloads, _ = _install_fakes(
        monkeypatch, currents=currents
    )
    task_module.task(ctx)
    assert not [command for command in writes if "InputMethod" in command]
    assert reloads == []


def test_automatic_look_and_feel_skips_theme_and_enables_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the native day and night switch on, the task applies no fixed
    # theme; it writes the AutomaticLookAndFeel keys instead.
    ctx = make_context(
        install_mode="desktop",
        force_tasks=frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_settings_home_dir=str(tmp_path),
            kde_settings_automatic_look_and_feel=True,
        ),
    )
    themes, schemes, _, _, writes, _, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert themes == []
    assert schemes == []
    auto_writes = [
        command for command in writes if "AutomaticLookAndFeel" in command
    ]
    assert auto_writes
    assert "--type" in auto_writes[0] and "bool" in auto_writes[0]
    interval_writes = [
        command
        for command in writes
        if "AutomaticLookAndFeelIdleInterval" in command
    ]
    assert interval_writes
    assert interval_writes[0][-1] == "99"


def test_cursor_theme_applied_when_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A differing cursorTheme is applied with plasma-apply-cursortheme.
    ctx = _ctx(tmp_path)
    _, _, _, _, _, _, cursorthemes = _install_fakes(
        monkeypatch, currents={"cursorTheme": "breeze_cursors"}
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert cursorthemes
    assert "Oxygen_Yellow" in " ".join(cursorthemes[0])
    assert "plasma-apply-cursortheme" in " ".join(cursorthemes[0])


def test_cursor_theme_skips_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The already applied cursorTheme skips the cursor apply.
    ctx = _ctx(tmp_path)
    _, _, _, _, _, _, cursorthemes = _install_fakes(
        monkeypatch, currents={"cursorTheme": "Oxygen_Yellow"}
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert cursorthemes == []


def test_cursor_theme_force_applies_even_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force mode applies the cursor theme regardless of the current state.
    ctx = _ctx(tmp_path, force=True)
    _, _, _, _, _, _, cursorthemes = _install_fakes(
        monkeypatch, currents={"cursorTheme": "Oxygen_Yellow"}
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert cursorthemes
    assert "Oxygen_Yellow" in " ".join(cursorthemes[0])


def test_cursor_theme_applied_after_kconfig_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cursor theme apply runs after the kconfig records, so it wins
    # over any theme default the records or the day and night switch
    # write.
    records = (
        KConfigRecord(
            "kcminputrc", ("Mouse",), "cursorTheme", "breeze_cursors", "string", False
        ),
    )
    ctx = _kconfig_ctx(tmp_path, records)
    _, _, _, _, writes, _, cursorthemes = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any("cursorTheme" in command for command in writes)
    assert cursorthemes
    assert "Oxygen_Yellow" in " ".join(cursorthemes[0])


def _make_system_theme(root: Path, name: str, cursor: str = "breeze_cursors") -> None:
    """Create a minimal system look and feel theme under root."""

    defaults = root / name / "contents" / "defaults"
    defaults.parent.mkdir(parents=True, exist_ok=True)
    defaults.write_text(
        f"[kcminputrc][Mouse]\ncursorTheme={cursor}\n", encoding="utf-8"
    )
    (root / name / "metadata.json").write_text("{}", encoding="utf-8")


def test_theme_cursor_overrides_copies_themes_with_cursors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both configured themes are copied into the user look and feel
    # directory and their defaults carry the configured cursor themes.
    system = tmp_path / "system-look-and-feel"
    _make_system_theme(system, "org.kubuntudark.desktop")
    _make_system_theme(system, "org.kubuntulight.desktop")
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    monkeypatch.setattr(task_module, "SYSTEM_LOOK_AND_FEEL_DIR", system)
    changed = task_module._apply_theme_cursor_overrides(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is True
    user_dir = tmp_path / ".local/share/plasma/look-and-feel"
    dark_defaults = user_dir / "org.kubuntudark.desktop/contents/defaults"
    light_defaults = user_dir / "org.kubuntulight.desktop/contents/defaults"
    assert dark_defaults.is_file()
    assert light_defaults.is_file()
    defaults_writes = [
        command for command in writes if "defaults" in " ".join(command)
    ]
    assert any("Oxygen_Yellow" in command for command in defaults_writes)
    assert any("Oxygen_Blue" in command for command in defaults_writes)


def test_theme_cursor_overrides_skip_missing_system_themes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing system theme is not an error; the copy is skipped.
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch)
    changed = task_module._apply_theme_cursor_overrides(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is False


def test_theme_cursor_overrides_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A second pass with matching cursor values changes nothing.
    system = tmp_path / "system-look-and-feel"
    _make_system_theme(system, "org.kubuntudark.desktop")
    _make_system_theme(system, "org.kubuntulight.desktop")
    monkeypatch.setattr(task_module, "SYSTEM_LOOK_AND_FEEL_DIR", system)
    ctx = _ctx(tmp_path)
    values: dict[tuple[str, str], str] = {}
    writes: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        if command[:4] == ["runuser", "-u", "i", "--"]:
            inner = command[4:]
            if inner[0] == "kreadconfig6":
                file = inner[inner.index("--file") + 1]
                key = inner[inner.index("--key") + 1]
                return _FakeProc(0, values.get((file, key), ""))
            if inner[0] == "kwriteconfig6":
                file = inner[inner.index("--file") + 1]
                key = inner[inner.index("--key") + 1]
                writes.append(list(command))
                values[(file, key)] = inner[-1]
                return _FakeProc(0, "")
        if command[0] in ("chown", "chmod"):
            return _FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    changed = task_module._apply_theme_cursor_overrides(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is True
    assert writes
    changed2 = task_module._apply_theme_cursor_overrides(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed2 is False


SHORTCUTS_RC = """\
[kwin]
Show Desktop=Meta+D,Meta+D,Peek at Desktop
ClearMouseMarks=Meta+Shift+F11,Meta+Shift+F11,Clear Mouse Marks
[org.kde.krunner.desktop]
clipboard_action=Meta+Ctrl+X,Meta+Ctrl+X,Automatic Action Popup Menu
"""


def test_shortcut_primaries_collects_owned_keys(tmp_path: Path) -> None:
    # Only the non-delete kglobalshortcutsrc records with a comma value
    # own a primary key.
    records = (
        KConfigRecord(
            "kglobalshortcutsrc",
            ("kwin",),
            "MinimizeAll",
            "Meta+D,none,Minimize all windows",
            "string",
            False,
        ),
        KConfigRecord(
            "kglobalshortcutsrc",
            ("kwin",),
            "ClearMouseMarks",
            "Meta+Shift+F11,Meta+Shift+F11,Clear Mouse Marks",
            "string",
            False,
        ),
        KConfigRecord(
            "kglobalshortcutsrc",
            ("kwin",),
            "MinimizeAllActiveScreen",
            "",
            "string",
            True,
        ),
        KConfigRecord("kwinrc", ("TabBox",), "LayoutName", "coverswitch", "string", False),
    )
    cfg = make_config(
        task_data_root=tmp_path, kde_settings_kconfig=records
    ).kde_settings
    owned = task_module._shortcut_primaries(cfg)
    assert owned == {
        "Meta+D": ["MinimizeAll"],
        "Meta+Shift+F11": ["ClearMouseMarks"],
    }


def test_clear_shortcut_conflicts_unbinds_other_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The action sharing a configured primary key is unbound; the
    # configured shortcuts and unrelated actions are left alone.
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "kglobalshortcutsrc").write_text(
        SHORTCUTS_RC, encoding="utf-8"
    )
    records = (
        KConfigRecord(
            "kglobalshortcutsrc",
            ("kwin",),
            "MinimizeAll",
            "Meta+D,none,Minimize all windows",
            "string",
            False,
        ),
        KConfigRecord(
            "kglobalshortcutsrc",
            ("kwin",),
            "ClearMouseMarks",
            "Meta+Shift+F11,Meta+Shift+F11,Clear Mouse Marks",
            "string",
            False,
        ),
    )
    ctx = _kconfig_ctx(tmp_path, records)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    cleared = task_module._clear_shortcut_conflicts(
        ctx.config.kde_settings, timeout=5
    )
    assert cleared is True
    show_writes = [command for command in writes if "Show Desktop" in command]
    assert show_writes
    assert show_writes[0][-1] == "none,none,Peek at Desktop"
    assert not [command for command in writes if "ClearMouseMarks" in command]
    assert not [command for command in writes if "clipboard_action" in command]


def test_clear_shortcut_conflicts_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # After the conflict is cleared its primary is none, so a second pass
    # changes nothing.
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "kglobalshortcutsrc").write_text(
        "[kwin]\nShow Desktop=none,none,Peek at Desktop\n",
        encoding="utf-8",
    )
    records = (
        KConfigRecord(
            "kglobalshortcutsrc",
            ("kwin",),
            "MinimizeAll",
            "Meta+D,none,Minimize all windows",
            "string",
            False,
        ),
    )
    ctx = _kconfig_ctx(tmp_path, records)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    cleared = task_module._clear_shortcut_conflicts(
        ctx.config.kde_settings, timeout=5
    )
    assert cleared is False
    assert writes == []


def test_user_dirs_merged_replaces_in_place_and_keeps_others() -> None:
    # A matching directive keeps the line, a differing one is replaced in
    # place, missing directives are appended, comments and foreign keys
    # survive.
    current = (
        "# comment\n"
        'XDG_DESKTOP_DIR="$HOME/Desktop"\n'
        'XDG_DOCUMENTS_DIR="$HOME/Documents"\n'
        'XDG_MUSIC_DIR="$HOME/Downloads"\n'
        "XDG_UNRELATED=value\n"
    )
    user_dirs = {
        "XDG_DOCUMENTS_DIR": "$HOME/Downloads",
        "XDG_MUSIC_DIR": "$HOME/Downloads",
    }
    merged = task_module._user_dirs_merged(current, user_dirs)
    lines = merged.splitlines()
    assert lines[0] == "# comment"
    assert lines[1] == 'XDG_DESKTOP_DIR="$HOME/Desktop"'
    assert 'XDG_DOCUMENTS_DIR="$HOME/Downloads"' in lines
    assert 'XDG_MUSIC_DIR="$HOME/Downloads"' in lines
    assert "XDG_UNRELATED=value" in lines


def test_apply_user_dirs_writes_configured_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The configured XDG dirs replace the existing ones and a second pass
    # changes nothing.
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "user-dirs.dirs").write_text(
        'XDG_DESKTOP_DIR="$HOME/Desktop"\nXDG_MUSIC_DIR="$HOME/Music"\n',
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch)
    changed = task_module._apply_user_dirs(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is True
    text = (config_dir / "user-dirs.dirs").read_text(encoding="utf-8")
    assert 'XDG_MUSIC_DIR="$HOME/Downloads"' in text
    assert 'XDG_DESKTOP_DIR="$HOME/Desktop"' in text
    changed2 = task_module._apply_user_dirs(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed2 is False


def test_apply_konsole_profile_renders_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The template is rendered with the configured home dir and written
    # under the user local share directory; a second pass is a no-op.
    asset = tmp_path / "Pyntara.profile"
    asset.write_text(
        "Directory={home_dir}/Downloads/\nName=Pyntara\n", encoding="utf-8"
    )
    monkeypatch.setattr(task_module, "KONSOLE_PROFILE_TEMPLATE", asset)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch)
    changed = task_module._apply_konsole_profile(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is True
    target = tmp_path / ".local/share/konsole/Pyntara.profile"
    expected = f"Directory={tmp_path}/Downloads/\nName=Pyntara\n"
    assert target.read_text(encoding="utf-8") == expected
    changed2 = task_module._apply_konsole_profile(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed2 is False


def _script_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: bool = True,
) -> tuple[list[list[str]], list[list[str]]]:
    """Replace run_command for the KWin script install helpers.

    The fake answers the mkdir, kreadconfig6, kwriteconfig6, the system
    python3 and chown/chmod commands the install and hotkey steps run;
    writes and live releases are recorded.
    """

    writes: list[list[str]] = []
    releases: list[list[str]] = []
    current_plugins: dict[str, str] = {}

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        if command[:4] == ["runuser", "-u", "i", "--"]:
            inner = command[4:]
            if inner[0] == "mkdir":
                return _FakeProc(0, "")
            if inner[0] == "kreadconfig6":
                key = inner[inner.index("--key") + 1]
                return _FakeProc(0, current_plugins.get(key, ""))
            if inner[0] == "kwriteconfig6":
                writes.append(list(command))
                if "kwinrc" in command and inner[-1] == "true":
                    key = inner[inner.index("--key") + 1]
                    current_plugins[key] = "true"
                return _FakeProc(0, "")
            if inner[0] == "/usr/bin/python3":
                releases.append(list(command))
                return _FakeProc(0, "")
        if command[0] in ("chown", "chmod"):
            return _FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    monkeypatch.setattr(
        task_module,
        "session_bus_address",
        (
            lambda username, timeout: "unix:path=/run/user/1000/bus"
            if session
            else None
        ),
    )
    return writes, releases


def _write_script_templates(root: Path) -> None:
    """Write minimal KWin script templates under the given root."""

    for script in task_module.KWIN_SCRIPTS:
        for rel_file in task_module.KWIN_SCRIPT_FILES:
            target = root / script / rel_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rel_file, encoding="utf-8")


def test_apply_kwin_scripts_installs_and_enables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Templates are copied into the user kwin scripts directory and the
    # scripts are enabled in kwinrc [Plugins]; a second pass is a no-op.
    template_root = tmp_path / "kwin"
    _write_script_templates(template_root)
    monkeypatch.setattr(task_module, "KWIN_SCRIPTS_TEMPLATE_ROOT", template_root)
    ctx = _ctx(tmp_path)
    writes, _ = _script_fakes(monkeypatch)
    changed = task_module._apply_kwin_scripts(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is True
    for script in task_module.KWIN_SCRIPTS:
        for rel_file in task_module.KWIN_SCRIPT_FILES:
            target = tmp_path / ".local/share/kwin/scripts" / script / rel_file
            assert target.read_text(encoding="utf-8") == rel_file
    enabled = [
        command[command.index("--key") + 1]
        for command in writes
        if "kwriteconfig6" in command and "kwinrc" in command
    ]
    for script in task_module.KWIN_SCRIPTS:
        assert f"{script}Enabled" in enabled
    changed2 = task_module._apply_kwin_scripts(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed2 is False


def test_apply_kwin_scripts_missing_templates_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No templates: the step changes nothing and is not an error.
    monkeypatch.setattr(
        task_module, "KWIN_SCRIPTS_TEMPLATE_ROOT", tmp_path / "missing"
    )
    ctx = _ctx(tmp_path)
    writes, _ = _script_fakes(monkeypatch)
    changed = task_module._apply_kwin_scripts(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is False
    assert writes == []


def test_script_hotkey_owners_finds_foreign_and_skips_own() -> None:
    # A foreign action that owns a script hotkey is returned; the
    # scripts' own actions are not.
    text = (
        "[kwin]\n"
        "Switch One Desktop Up=Meta+Ctrl+Up,Meta+Ctrl+Up,Switch One Desktop Up\n"
        "Grow Window by 5px=Meta+Ctrl+Up,none,Grow Window by 5px\n"
        "Window Maximize=Meta+PgUp,Meta+PgUp,Maximize Window\n"
    )
    owners = task_module._script_hotkey_owners(text)
    assert owners == [
        (("kwin",), "Switch One Desktop Up", "Switch One Desktop Up")
    ]


def test_free_script_hotkeys_clears_and_releases_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The foreign owner is cleared in the config and the running daemon
    # is asked to release the key through python3-dbus.
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "kglobalshortcutsrc").write_text(
        "[kwin]\n"
        "Switch One Desktop Up=Meta+Ctrl+Up,Meta+Ctrl+Up,Switch One Desktop Up\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    writes, releases = _script_fakes(monkeypatch, session=True)
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
    changed = task_module._free_script_hotkeys(
        ctx.config.kde_settings, env=env, timeout=5
    )
    assert changed is True
    cleared = [command for command in writes if "Switch One Desktop Up" in command]
    assert cleared
    assert cleared[0][-1] == "none,none,Switch One Desktop Up"
    assert releases
    assert releases[0][:4] == ["runuser", "-u", "i", "--"]
    assert "/usr/bin/python3" in releases[0]


def test_free_script_hotkeys_without_session_skips_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a session bus the config is still cleared, but no daemon
    # release runs.
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "kglobalshortcutsrc").write_text(
        "[kwin]\n"
        "Switch One Desktop Down=Meta+Ctrl+Down,none,Switch One Desktop Down\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    _, releases = _script_fakes(monkeypatch, session=False)
    changed = task_module._free_script_hotkeys(
        ctx.config.kde_settings, env={}, timeout=5
    )
    assert changed is True
    assert releases == []


def test_kwin_scripts_installed_and_hotkeys_freed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The full task installs the scripts, enables them and frees the
    # script hotkeys from the foreign actions.
    template_root = tmp_path / "kwin"
    _write_script_templates(template_root)
    monkeypatch.setattr(task_module, "KWIN_SCRIPTS_TEMPLATE_ROOT", template_root)
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "kglobalshortcutsrc").write_text(
        "[kwin]\n"
        "Switch One Desktop Up=Meta+Ctrl+Up,Meta+Ctrl+Up,Switch One Desktop Up\n"
        "Switch One Desktop Down=Meta+Ctrl+Down,Meta+Ctrl+Down,Switch One Desktop Down\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, bus_pid="")
    result = task_module.task(ctx)
    assert result.success is True
    for script in task_module.KWIN_SCRIPTS:
        for rel_file in task_module.KWIN_SCRIPT_FILES:
            target = tmp_path / ".local/share/kwin/scripts" / script / rel_file
            assert target.read_text(encoding="utf-8") == rel_file
    cleared = [
        command
        for command in writes
        if "Switch One Desktop Up" in command
        or "Switch One Desktop Down" in command
    ]
    assert len(cleared) == 2


XBEL = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xbel>
<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks" xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info">
 <bookmark href="file:///home/i">
  <title>Home</title>
  <info>
   <metadata owner="http://freedesktop.org">
    <bookmark:icon name="user-home"/>
   </metadata>
   <metadata owner="http://www.kde.org">
    <ID>1787750121/0</ID>
    <isSystemItem>true</isSystemItem>
   </metadata>
  </info>
 </bookmark>
 <bookmark href="file:///home/i/Downloads">
  <title>Downloads</title>
  <info>
   <metadata owner="http://www.kde.org">
    <ID>1787750121/3</ID>
    <isSystemItem>true</isSystemItem>
   </metadata>
  </info>
 </bookmark>
 <separator>
  <info>
   <metadata owner="http://www.kde.org">
    <UDI>/org/freedesktop/UDisks2/block_devices/sda1</UDI>
    <uuid>e2696dd4-8a28-4562-9241-014cdda1546c</uuid>
   </metadata>
  </info>
 </separator>
</xbel>
"""


def test_places_xbel_hidden_adds_marker_for_hidden_titles() -> None:
    # Home is matched by its title and hidden, Downloads stays visible,
    # and the machine-specific device separator survives untouched.
    out = task_module._places_xbel_hidden(XBEL, {"Home"})
    assert out is not None
    home = out.split("<title>Home</title>")[1].split("</bookmark>")[0]
    assert "<IsHidden>true</IsHidden>" in home
    downloads = out.split("<title>Downloads</title>")[1].split("</bookmark>")[0]
    assert "<IsHidden>" not in downloads
    assert "e2696dd4-8a28-4562-9241-014cdda1546c" in out


def test_places_xbel_hidden_idempotent() -> None:
    # A second pass over an already hidden file changes nothing.
    out = task_module._places_xbel_hidden(XBEL, {"Home"})
    assert out is not None
    assert task_module._places_xbel_hidden(out, {"Home"}) is None


def test_apply_places_hidden_writes_when_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Places file gets the IsHidden marker and a second pass changes
    # nothing.
    places_dir = tmp_path / ".local/share"
    places_dir.mkdir(parents=True, exist_ok=True)
    (places_dir / "user-places.xbel").write_text(XBEL, encoding="utf-8")
    ctx = make_context(
        install_mode="desktop",
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_settings_home_dir=str(tmp_path),
            kde_settings_places_hidden=("Home",),
        ),
    )
    _install_fakes(monkeypatch)
    changed = task_module._apply_places_hidden(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is True
    text = (places_dir / "user-places.xbel").read_text(encoding="utf-8")
    assert "IsHidden" in text
    changed2 = task_module._apply_places_hidden(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed2 is False


def test_apply_places_hidden_skips_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A file that already hides the configured places changes nothing.
    already = task_module._places_xbel_hidden(XBEL, {"Home"})
    assert already is not None
    places_dir = tmp_path / ".local/share"
    places_dir.mkdir(parents=True, exist_ok=True)
    (places_dir / "user-places.xbel").write_text(already, encoding="utf-8")
    ctx = make_context(
        install_mode="desktop",
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_settings_home_dir=str(tmp_path),
            kde_settings_places_hidden=("Home",),
        ),
    )
    _install_fakes(monkeypatch)
    changed = task_module._apply_places_hidden(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is False


def test_apply_places_hidden_missing_file_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without the Places file the hiding is skipped and not an error.
    ctx = make_context(
        install_mode="desktop",
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_settings_home_dir=str(tmp_path),
            kde_settings_places_hidden=("Home",),
        ),
    )
    _install_fakes(monkeypatch)
    changed = task_module._apply_places_hidden(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is False


def test_touchpad_clickareas_writes_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The clickareas method maps to the ClickMethod value 2.
    ctx = make_context(
        install_mode="desktop",
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            kde_settings_home_dir=str(tmp_path),
            kde_settings_touchpad_click_method="clickareas",
        ),
    )
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "kcminputrc").write_text(TOUCHPAD_RC, encoding="utf-8")
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    click_writes = [command for command in writes if "ClickMethod" in command]
    assert click_writes
    assert click_writes[0][-1] == "2"


def test_apply_sddm_writes_system_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The autologin and theme values are written to the system files.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    changed = task_module._apply_sddm(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is True
    assert len(writes) == 6
    assert "/etc/sddm.conf" in " ".join(writes[0])
    assert writes[0][-1] == "i"
    assert "/etc/sddm.conf.d/20-kubuntu.conf" in " ".join(writes[5])
    assert writes[5][-1] == "Noto Sans,20"


def test_apply_sddm_idempotent_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Matching system values change nothing.
    ctx = _ctx(tmp_path)
    currents = {
        "User": "i",
        "Session": "plasma",
        "Current": "kubuntu",
        "CursorSize": "30",
        "CursorTheme": "breeze_cursors",
        "Font": "Noto Sans,20",
    }
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, currents=currents)
    changed = task_module._apply_sddm(
        ctx.config.kde_settings, timeout=5, force=False
    )
    assert changed is False
    assert writes == []


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


def _preconfigure_user_files(tmp_path: Path, cfg) -> None:
    """Write the user-dirs.dirs and the Konsole profile the task expects,
    so an idempotent run sees them as already configured."""

    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "user-dirs.dirs").write_text(
        task_module._user_dirs_merged("", cfg.user_dirs), encoding="utf-8"
    )
    profile = task_module.KONSOLE_PROFILE_TEMPLATE.read_text(encoding="utf-8")
    profile = profile.replace("{home_dir}", cfg.home_dir)
    profile_dir = tmp_path / ".local/share/konsole"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "Pyntara.profile").write_text(profile, encoding="utf-8")
    for script in task_module.KWIN_SCRIPTS:
        for rel_file in task_module.KWIN_SCRIPT_FILES:
            template = task_module.KWIN_SCRIPTS_TEMPLATE_ROOT / script / rel_file
            target = tmp_path / ".local/share/kwin/scripts" / script / rel_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")


FULLY_CONFIGURED = {
    "LookAndFeelPackage": "org.kubuntudark.desktop",
    "ColorScheme": "BreezeDark",
    "NumLock": "1",
    "InputMethod": "/usr/share/applications/org.kde.plasma.keyboard.desktop",
    "enabledLocales": "en_US,es_MX,ru_RU",
    "User": "i",
    "Session": "plasma",
    "Current": "kubuntu",
    "CursorSize": "30",
    "CursorTheme": "breeze_cursors",
    "cursorTheme": "Oxygen_Yellow",
    "Font": "Noto Sans,20",
    "window-grow-shrinkEnabled": "true",
    "window-restore-trackerEnabled": "true",
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
    _, _, _, _, writes, _, _ = _install_fakes(
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
    _preconfigure_user_files(tmp_path, ctx.config.kde_settings)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, currents=currents)
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
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, currents=currents)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert [command for command in writes if "LayoutName" in command]
