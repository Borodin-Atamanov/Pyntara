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
    _, _, _, _, writes, reloads, _ = _install_fakes(monkeypatch)
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
