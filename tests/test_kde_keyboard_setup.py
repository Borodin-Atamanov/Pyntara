"""Unit tests for the kde_keyboard_setup task.

All external resources (subprocess, the process environment, package
state) are mocked via monkeypatch; the tests only touch temporary
fixtures (docs/guides/developer-guide.md). The fake run_command inspects
the command shape and answers per key.
"""

from __future__ import annotations

import json
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


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    appletsrc: str = SAMPLE_APPLETSRC,
    hotkeys: dict[str, str] | None = None,
):
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
            kde_keyboard_setup_layout_switch_shortcuts=hotkeys,
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
    fail_live_apply: bool = False,
    live_apply_error_stderr: str = "",
    hotkey_state: dict[str, list[int]] | None = None,
):
    """Replace run_command and package state; return captured command lists.

    currents maps a KConfig key name to its current value, so a key whose
    value matches the target skips the write. bus_pid empty disables the
    desktop session lookup. hotkey_state maps a hotkey action name to the
    keys the daemon reports before the live apply, for idempotency tests.
    """

    currents = currents or {}
    writes: list[list[str]] = []
    reloads: list[list[str]] = []
    restarts: list[list[str]] = []
    installs: list[str] = []
    live_applies: list[list[str]] = []

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
            if inner[0] == "python3":
                live_applies.append(list(command))
                if fail_live_apply:
                    raise subprocess.CalledProcessError(
                        1, command, stderr=live_apply_error_stderr
                    )
                payload = json.loads(inner[-1])
                before: dict[str, list[int]] = {}
                after: dict[str, list[int]] = {}
                for action, combined in payload["assign"]:
                    before[action] = list((hotkey_state or {}).get(action, []))
                    after[action] = [combined]
                return _FakeProc(0, json.dumps({"before": before, "after": after}))
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
    return writes, reloads, restarts, installs, live_applies


def test_first_run_writes_and_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No current values: every kxkbrc key and the display style are
    # written, then kwin is reloaded and the panel restarted.
    ctx = _ctx(tmp_path)
    writes, reloads, restarts, installs, _ = _install_fakes(monkeypatch)
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
    writes, reloads, restarts, _, _ = _install_fakes(
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
    writes, reloads, restarts, _, _ = _install_fakes(
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
    _, _, _, installs, _ = _install_fakes(monkeypatch, installed=False)
    result = task_module.task(ctx)
    assert result.success is True
    assert installs == ["libkf6config-bin", "qdbus-qt6", "python3-dbus"]


def test_package_install_failure_is_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed package install is a warning; without the kwriteconfig6
    # provider the config writes are skipped and the task still completes.
    ctx = _ctx(tmp_path)
    writes, _, _, _, _ = _install_fakes(
        monkeypatch, installed=False, fail_install=True
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert any("cannot install" in warning for warning in result.warnings)
    assert writes == []


def test_no_desktop_session_skips_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a kwin_wayland process the reload is skipped, not fatal.
    ctx = _ctx(tmp_path)
    writes, reloads, _, _, _ = _install_fakes(monkeypatch, bus_pid="")
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
    writes, _, restarts, _, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    display_writes = [
        command for command in writes if "appletsrc" in " ".join(command)
    ]
    assert display_writes == []
    assert restarts == []


def test_write_failure_is_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing kwriteconfig6 is a warning and the remaining steps still
    # run; the task completes with the collected warnings.
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, fail_on_write=True)
    result = task_module.task(ctx)
    assert result.success is True
    assert any("cannot write" in warning for warning in result.warnings)


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


SPANISH_ACTION = "Switch keyboard layout to Spanish"
HOTKEYS = {SPANISH_ACTION: "Meta+Q"}
META_Q = 0x10000000 | 0x51


def test_shortcut_to_combined_parses_modifiers_and_key() -> None:
    # Modifiers plus one alphanumeric key become the combined Qt key code.
    assert task_module._shortcut_to_combined("Meta+Q") == META_Q
    assert task_module._shortcut_to_combined("Ctrl+Alt+E") == (
        0x04000000 | 0x08000000 | 0x45
    )
    assert task_module._shortcut_to_combined("Shift+Meta+1") == (
        0x02000000 | 0x10000000 | 0x31
    )
    assert task_module._shortcut_to_combined("Q") == 0x51


def test_shortcut_to_combined_rejects_unsupported() -> None:
    # Function keys, named keys and a bare modifier are not supported.
    assert task_module._shortcut_to_combined("F5") is None
    assert task_module._shortcut_to_combined("Space") is None
    assert task_module._shortcut_to_combined("Meta") is None
    assert task_module._shortcut_to_combined("") is None


def test_no_session_writes_hotkey_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a desktop session the hotkey is written to kglobalshortcutsrc
    # and the live apply is skipped.
    ctx = _ctx(tmp_path, hotkeys=HOTKEYS)
    writes, _, _, _, live_applies = _install_fakes(monkeypatch, bus_pid="")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert live_applies == []
    hotkey_writes = [
        command
        for command in writes
        if "kglobalshortcutsrc" in " ".join(command)
    ]
    assert any(
        SPANISH_ACTION in " ".join(command)
        and f"Meta+Q,none,{SPANISH_ACTION}" in " ".join(command)
        for command in hotkey_writes
    )


def test_no_session_hotkey_already_set_skips_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When the kglobalshortcutsrc entry already matches, nothing changes.
    ctx = _ctx(tmp_path, hotkeys=HOTKEYS)
    currents = {
        "LayoutList": "us,ru,es",
        "Options": "grp:caps_select",
        "Use": "true",
        "displayStyle": "Flag",
        SPANISH_ACTION: f"Meta+Q,none,{SPANISH_ACTION}",
    }
    writes, reloads, restarts, _, live_applies = _install_fakes(
        monkeypatch, bus_pid="", currents=currents
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert writes == []
    assert reloads == []
    assert restarts == []
    assert live_applies == []


def test_session_applies_hotkey_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With a desktop session the hotkey is applied through the daemon: the
    # python3 client runs as the user with the correct payload.
    ctx = _ctx(tmp_path, hotkeys=HOTKEYS)
    _, _, _, _, live_applies = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert len(live_applies) == 1
    payload = json.loads(live_applies[0][-1])
    assert payload["component_unique"] == "KDE Keyboard Layout Switcher"
    assert payload["component_friendly"] == "Keyboard Layout Switcher"
    assert [SPANISH_ACTION, META_Q] in payload["assign"]


def test_session_hotkey_already_applied_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When the file entry and the daemon already carry the shortcut, the
    # task reports no change (the live client still runs and confirms).
    ctx = _ctx(tmp_path, hotkeys=HOTKEYS)
    currents = {
        "LayoutList": "us,ru,es",
        "Options": "grp:caps_select",
        "Use": "true",
        "displayStyle": "Flag",
        SPANISH_ACTION: f"Meta+Q,none,{SPANISH_ACTION}",
    }
    writes, reloads, restarts, _, live_applies = _install_fakes(
        monkeypatch,
        currents=currents,
        hotkey_state={SPANISH_ACTION: [META_Q]},
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert writes == []
    assert reloads == []
    assert restarts == []
    assert len(live_applies) == 1


def test_session_hotkey_apply_failure_is_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing live apply is a warning and the task still completes, so
    # the reload and the panel restart are not skipped.
    ctx = _ctx(tmp_path, hotkeys=HOTKEYS)
    _, reloads, restarts, _, _ = _install_fakes(
        monkeypatch, fail_live_apply=True
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert any(
        "cannot apply layout hotkeys" in warning for warning in result.warnings
    )
    assert reloads
    assert restarts


def test_session_hotkey_apply_failure_reports_client_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The warning carries the client error output, so a recurring live
    # apply failure is diagnosable from the task log alone.
    ctx = _ctx(tmp_path, hotkeys=HOTKEYS)
    _install_fakes(
        monkeypatch,
        fail_live_apply=True,
        live_apply_error_stderr="Traceback (most recent call last):\nNameError\n",
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert any("NameError" in warning for warning in result.warnings)


def test_unsupported_shortcut_is_written_not_applied_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A function-key shortcut cannot be applied live but is still written
    # to the config file.
    ctx = _ctx(tmp_path, hotkeys={SPANISH_ACTION: "F5"})
    writes, _, _, _, live_applies = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert live_applies == []
    hotkey_writes = [
        command
        for command in writes
        if "kglobalshortcutsrc" in " ".join(command)
    ]
    assert any(SPANISH_ACTION in " ".join(command) for command in hotkey_writes)
