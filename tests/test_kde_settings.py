"""Unit tests for the kde_settings task.

All external resources (subprocess, the session bus, package state) are
mocked via monkeypatch; the tests only touch temporary fixtures. The fake
run_command inspects the command shape and answers per key. The home of the
desktop user and every value of the section are module values, so one
autouse fixture points them at the temporary tree of the test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara.tasks import kde_settings as task_module
from pyntara.utils import kglobalaccel_names
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import kde_settings as values
from pyntara.values.kde_settings import KconfigRecord

# The templates of the task live in the clone the tests run from, so a test
# that pre-writes the files the task expects reads the shipped template.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# The shared python3-dbus client that frees a combination from whatever
# action holds it and gives it to a configured action; both the script
# hotkeys and the configured shortcut records are applied through it.
_SHARED_CLIENT = _REPO_ROOT / "task_data" / "kde_keyboard_setup" / "apply_hotkeys.py"


def _shared_client(tmp_path: Path) -> Path:
    """The shared client copied into the temporary tree of the test.

    The task renders the client it runs next to the file it read, so a test
    hands over a copy of its own: the shipped client of the repository is
    read and never written.
    """

    path = tmp_path / _SHARED_CLIENT.name
    path.write_text(_SHARED_CLIENT.read_text(encoding="utf-8"), encoding="utf-8")
    return path

# Shortcut records as the config carries them: the description field is not
# read, the absent word and an empty field mean no combination, and a record
# of another file is a plain KConfig value.
_SHORTCUT_RECORDS = (
    KconfigRecord(
        file="kglobalshortcutsrc",
        group=("kwin",),
        key="Walk Through Windows",
        value="Alt+Tab,none,Walk Through Windows",
        delete=False,
    ),
    KconfigRecord(
        file="kglobalshortcutsrc",
        group=("kwin",),
        key="MinimizeAll",
        value="Meta+D,meta+u,Minimize all windows",
        delete=False,
    ),
    KconfigRecord(
        file="kglobalshortcutsrc",
        group=("plasmashell",),
        key="manage activities",
        value="none,none,Show Activity Switcher",
        delete=False,
    ),
    KconfigRecord(
        file="kwinrc",
        group=("TabBox",),
        key="LayoutName",
        value="thumbnail_grid",
        delete=False,
    ),
)


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own target tree and the shipped values.

    The home of the desktop user and the values of the section are module
    values, so the fixture points the home at the temporary directory of the
    test and registers every value for restoration: a value a test points at
    its own fixture comes back after that test.
    """

    monkeypatch.setattr(common_values, "DESKTOP_USERNAME", "i")
    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(tmp_path))
    for name in values.READ_VALUE_NAMES:
        monkeypatch.setattr(values, name, getattr(values, name))
    for name in common_values.READ_VALUE_NAMES:
        monkeypatch.setattr(common_values, name, getattr(common_values, name))


def _temporary_clone(tmp_path: Path) -> Path:
    """A clone tree of the test carrying the task data the task reads.

    The task renders the client it runs next to the file it read, so a test
    that runs the whole task hands it a clone of its own: the shipped task
    data of the sections the task reads is copied into the temporary
    directory of the test, and the repository is only read.
    """

    clone_root = tmp_path / "clone"
    for section in ("kde_settings", "kde_keyboard_setup"):
        shutil.copytree(
            _REPO_ROOT / "task_data" / section,
            clone_root / "task_data" / section,
            dirs_exist_ok=True,
        )
    return clone_root


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    kcminputrc: str | None = None,
    virtual_keyboard_enabled: bool = True,
    system_look_and_feel_dir: Path | None = None,
    repo_root: Path | None = None,
    kconfig: tuple[KconfigRecord, ...] = (),
    automatic_look_and_feel: int = 0,
):
    """Context with the target user home and the values rooted in tmp_path.

    kcminputrc, when given, is written into the user config directory so
    the touchpad discovery reads it. system_look_and_feel_dir is the system
    theme directory of the test; the default one does not exist, so the
    theme cursor overrides skip the copy unless a test points it at its own
    fixture. automatic_look_and_feel is off unless a test asks for the
    native day and night switch, so a task test applies the dark theme
    directly and the switch has its own tests.
    """

    if kcminputrc is not None:
        config_dir = tmp_path / ".config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "kcminputrc").write_text(kcminputrc, encoding="utf-8")
    values.VIRTUAL_KEYBOARD_ENABLED = virtual_keyboard_enabled
    values.AUTOMATIC_LOOK_AND_FEEL = automatic_look_and_feel
    values.SYSTEM_LOOK_AND_FEEL_DIR = (
        system_look_and_feel_dir or tmp_path / "no-system-themes"
    )
    values.KCONFIG_RECORDS = kconfig
    return make_context(
        task_name="kde_settings",
        install_mode="desktop",
        force_tasks=frozenset({"kde_settings"}) if force else frozenset(),
        task_data_root=tmp_path,
        repo_root=repo_root if repo_root is not None else _temporary_clone(tmp_path),
    )


def _is_assign_call(inner: list[str]) -> bool:
    """True when a python client call carries the hotkey payload.

    The task runs one python client as the target user, the shared client
    that gives the configured combinations to the running daemon. Its call
    is the one whose last argument is its JSON payload.
    """

    return inner[-1].startswith("{")


def _assign_reply(
    inner: list[str],
    assign_calls: list[list[str]] | None,
    assign_state: dict[str, list[str]] | None,
    assign_after: dict[str, list[str]] | None,
    assign_missing: frozenset[str] | None = None,
    assign_unsupported: dict[str, list[str]] | None = None,
    assign_held_elsewhere: dict[str, list[list[Any]]] | None = None,
) -> _FakeProc:
    """The answer of the shortcut client: the state before and after.

    The request carries one change per action with the combinations as the
    portable text the config names; the real client reports the combined
    Qt key codes, and the fake reports the same text, because the task
    compares the two lists inside one report and never converts a
    combination itself. assign_state is the state an action already
    holds, so a machine whose combinations are granted reports the same
    state before and after the call and nothing changes. assign_after
    replaces the state an action holds after the call, so a test can make
    the client report another combination or none at all. assign_missing
    names actions the daemon lists no such name for: the client gives them
    their combinations anyway and only says so in the report, which is what
    a per-layout action of the keyboard layout switcher does. assign_unsupported
    names combinations Qt cannot read, and assign_held_elsewhere names, per
    action, the action that kept one of its combinations, as [code, component
    unique, action unique, component friendly, action friendly] tuples. The
    call is recorded as it ran, so a test reads the request the task passed to
    the interpreter.
    """

    if assign_calls is not None:
        assign_calls.append(list(inner))
    request = json.loads(inner[-1])
    held = assign_state or {}
    missing = assign_missing or frozenset()
    unsupported = assign_unsupported or {}
    held_elsewhere = assign_held_elsewhere or {}
    results = []
    for change in request["changes"]:
        action = change["action"]
        unreadable = list(unsupported.get(action, []))
        keys = [text for text in change["keys"] if text not in unreadable]
        after = keys
        if assign_after is not None and action in assign_after:
            after = list(assign_after[action])
        results.append(
            {
                "action": action,
                "requested": keys,
                "before": list(held.get(action, [])),
                "after": after,
                "unsupported": unreadable,
                "held_elsewhere": list(held_elsewhere.get(action, [])),
                "missing": action in missing,
            }
        )
    return _FakeProc(0, json.dumps({"results": results}))


def _granted_script_hotkeys() -> dict[str, list[str]]:
    """The client state of a machine whose script hotkeys are granted."""

    return {
        action: [hotkey]
        for action, hotkey in zip(
            values.KWIN_SCRIPT_ACTIONS, values.KWIN_SCRIPT_HOTKEYS
        )
    }


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    currents: dict[str, str] | None = None,
    bus_pid: str = "1763",
    installed: bool = True,
    fail_install: bool = False,
    fail_on_apply: bool = False,
    fail_on_write: bool = False,
    fail_on_write_keys: frozenset[str] | None = None,
    fail_on_reload: bool = False,
    assign_calls: list[list[str]] | None = None,
    assign_state: dict[str, list[str]] | None = None,
    assign_after: dict[str, list[str]] | None = None,
    assign_after_sequence: list[dict[str, list[str]]] | None = None,
    assign_missing: frozenset[str] | None = None,
    assign_missing_sequence: list[frozenset[str]] | None = None,
    assign_unsupported: dict[str, list[str]] | None = None,
    assign_held_elsewhere: dict[str, list[list[Any]]] | None = None,
):
    """Replace run_command, the session environment and package state.

    currents maps a KConfig key name to its current value, so a key whose
    value matches the target skips the write or apply. bus_pid empty
    disables the desktop session lookup. fail_on_write_keys fails only
    the writes of the named keys, so one bad value leaves the others
    alone. assign_calls collects the calls of the hotkey client,
    assign_state is the combination state an action already holds and
    assign_after overrides the state the client reports back;
    assign_after_sequence answers one state per call, which a test uses to
    let the first attempt fail and a later one take. assign_missing names
    actions the daemon does not know, assign_missing_sequence answers one
    set of unknown actions per call, which a test uses to let the daemon
    learn an action that kwin registers while the task waits, and
    assign_unsupported names combinations the client cannot read.
    """

    attempt = [0]
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
                key = inner[inner.index("--key") + 1]
                if fail_on_write_keys and key in fail_on_write_keys:
                    raise subprocess.CalledProcessError(1, command)
                writes.append(list(command))
                return _FakeProc(0, "")
            if inner[0] == "qdbus6":
                if fail_on_reload:
                    raise subprocess.CalledProcessError(1, command)
                reloads.append(list(command))
                return _FakeProc(0, "")
            if inner[0] == "/usr/bin/python3":
                if _is_assign_call(inner):
                    index = attempt[0]
                    attempt[0] += 1
                    after = assign_after
                    if assign_after_sequence:
                        after = assign_after_sequence[
                            min(index, len(assign_after_sequence) - 1)
                        ]
                    missing = assign_missing
                    if assign_missing_sequence:
                        missing = assign_missing_sequence[
                            min(index, len(assign_missing_sequence) - 1)
                        ]
                    return _assign_reply(
                        inner,
                        assign_calls,
                        assign_state,
                        after,
                        missing,
                        assign_unsupported,
                        assign_held_elsewhere,
                    )
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
        "session_environment",
        (
            lambda username, **kwargs: (
                {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
                if bus_pid
                else {}
            )
        ),
    )
    # The system theme directory of the default test config does not exist,
    # so the theme cursor overrides skip the copy in the general task tests;
    # the override tests point it at their own fixtures through _ctx.
    return themes, schemes, order, installs, writes, reloads, cursorthemes


def test_first_run_applies_both_themes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No current values: the global theme and the color scheme are both
    # applied, the global theme first.
    ctx = _ctx(tmp_path)
    themes, schemes, order, installs, _, _, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any("org.kubuntudark.desktop" in command for command in themes)
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
    _preconfigure_user_files(tmp_path)
    themes, schemes, order, _, writes, reloads, _ = _install_fakes(
        monkeypatch,
        currents=currents,
        assign_state=_granted_script_hotkeys(),
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
    themes, schemes, order, _, _, _, _ = _install_fakes(monkeypatch, currents=currents)
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
    themes, schemes, order, _, _, _, _ = _install_fakes(monkeypatch, currents=currents)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert themes == []
    assert schemes
    assert order == ["colorscheme"]


def test_only_theme_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The color scheme already matches, only the global theme differs.
    ctx = _ctx(tmp_path)
    currents = {
        "LookAndFeelPackage": "org.kde.breeze.desktop",
        "ColorScheme": "BreezeDark",
    }
    themes, schemes, order, _, _, _, _ = _install_fakes(monkeypatch, currents=currents)
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
    assert installs == list(values.PACKAGES)


def test_package_install_failure_is_a_warning_and_settings_still_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed package install is a recoverable failure: every package is
    # attempted, the failures are reported as warnings, and the settings
    # that do not need the package still apply, because a missing package
    # of one step must not leave the rest of the desktop unconfigured.
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, installed=False, fail_install=True)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert result.warnings
    assert any("cannot install" in warning for warning in result.warnings)
    assert result.message == (
        "KDE appearance and input settings configured with warnings"
    )


def test_appearance_tool_failure_does_not_fail_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A crashing plasma-apply tool is not fatal: the value was already
    # written into the config and applies at the next login.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, fail_on_apply=True)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    lookandfeel_writes = [
        command for command in writes if "LookAndFeelPackage" in command
    ]
    assert lookandfeel_writes


def test_no_desktop_session_still_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a kwin_wayland process the appearance values are written
    # into the config (they apply at the next login) and the GUI
    # plasma-apply tools are not invoked, so the run is not an error.
    ctx = _ctx(tmp_path)
    themes, schemes, _, _, writes, _, cursorthemes = _install_fakes(
        monkeypatch, bus_pid=""
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert not themes
    assert not schemes
    assert not cursorthemes
    lookandfeel_writes = [
        command for command in writes if "LookAndFeelPackage" in command
    ]
    assert lookandfeel_writes


def test_apply_env_carries_live_session_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A live desktop session contributes the bus address and the display
    # variables, so a GUI plasma-apply tool started over SSH still
    # connects to the running compositor.
    _ctx(tmp_path)
    monkeypatch.setattr(
        task_module,
        "session_environment",
        lambda username, **kwargs: {
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DISPLAY": ":0",
        },
    )
    env = task_module._apply_env()
    assert env is not None
    assert env["HOME"] == str(tmp_path)
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"


def test_apply_env_without_session_has_no_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No live session: the environment is absent, so the appearance values
    # are written for the next login and no GUI tool runs without a display.
    _ctx(tmp_path)
    monkeypatch.setattr(
        task_module, "session_environment", lambda username, **kwargs: {}
    )
    assert task_module._apply_env() is None


def test_written_user_file_mode_comes_from_the_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The chmod of a written user file carries the mode the caller passes,
    # which is the value of the section at every call site, so a stricter or
    # looser mode is answered in the values.
    chmods: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        if command[0] == "chmod":
            chmods.append(list(command))
        return _FakeProc(0, "")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    written = task_module._write_user_file(
        "notes.txt", "content", mode=0o640, timeout=5, force=True
    )
    assert written is True
    assert chmods == [["chmod", "0640", str(tmp_path / "notes.txt")]]


def test_one_config_failure_does_not_stop_other_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A single bad write (NumLock) is reported as a warning and the other
    # independent settings still apply.
    ctx = _ctx(tmp_path)
    themes, _, _, _, writes, _, _ = _install_fakes(
        monkeypatch, fail_on_write_keys=frozenset({"NumLock"})
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert themes
    assert any("NumLock" in warning for warning in result.warnings)
    lookandfeel_writes = [
        command for command in writes if "LookAndFeelPackage" in command
    ]
    assert lookandfeel_writes


def test_all_config_writes_failing_task_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every user kwriteconfig6 call fails: the task still reports success
    # with the collected warnings instead of dying, so a rerun can fix
    # them. The system SDDM writes do not go through runuser and still
    # run.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, fail_on_write=True)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.warnings
    assert not [command for command in writes if "LookAndFeelPackage" in command]
    assert (
        result.message == "KDE appearance and input settings configured with warnings"
    )


def test_reload_failure_is_a_warning_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing kwin reload does not discard the applied settings: it is
    # reported as a warning and the task still succeeds.
    ctx = _ctx(tmp_path)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, fail_on_reload=True)
    result = task_module.task(ctx)
    assert result.success is True
    assert writes
    assert result.warnings
    assert any("reload" in warning for warning in result.warnings)


TOUCHPAD_RC = """\
[Libinput][2362][597][SYNA3602:00 093A:0255 Touchpad]
ClickMethod=2

[Mouse]
cursorSize=72
"""


def test_touchpad_groups_finds_touchpad_sections() -> None:
    # Only the libinput groups whose device name ends with Touchpad match.
    assert task_module._touchpad_groups(
        TOUCHPAD_RC,
        values.TOUCHPAD_GROUP_ROOT,
        values.TOUCHPAD_DEVICE_WORD,
    ) == [("Libinput", "2362", "597", "SYNA3602:00 093A:0255 Touchpad")]
    assert (
        task_module._touchpad_groups(
            "[Mouse]\ncursorSize=72\n",
            values.TOUCHPAD_GROUP_ROOT,
            values.TOUCHPAD_DEVICE_WORD,
        )
        == []
    )


def test_the_touchpad_group_words_come_from_the_values() -> None:
    # The root group and the word a device name ends with are values of the
    # section: another pair of them is the group the task collects.
    text = "[MyRoot][1][2][name MyPad]\nClickMethod=2\n"
    assert task_module._touchpad_groups(text, "MyRoot", "MyPad") == [
        ("MyRoot", "1", "2", "name MyPad")
    ]
    assert task_module._touchpad_groups(text, "Libinput", "Touchpad") == []


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
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, currents={"NumLock": "1"})
    task_module.task(ctx)
    assert not [command for command in writes if "NumLock" in command]


def test_touchpad_writes_to_each_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The click method goes to every touchpad group in kcminputrc.
    ctx = _ctx(tmp_path, kcminputrc=TOUCHPAD_RC)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    click_writes = [command for command in writes if "ClickMethod" in command]
    assert click_writes
    assert "Libinput" in " ".join(click_writes[0])
    # The configured click method maps to the value the file stores.
    assert (
        click_writes[0][-1] == values.CLICK_METHOD_VALUES[values.TOUCHPAD_CLICK_METHOD]
    )


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
    _preconfigure_user_files(tmp_path)
    _, _, _, _, writes, reloads, _ = _install_fakes(monkeypatch, currents=currents)
    task_module.task(ctx)
    assert not [command for command in writes if "InputMethod" in command]
    assert reloads == []


def test_automatic_look_and_feel_skips_theme_and_enables_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the native day and night switch on, the task applies no fixed
    # theme; it writes the AutomaticLookAndFeel keys instead.
    ctx = _ctx(tmp_path, automatic_look_and_feel=1)
    themes, schemes, _, _, writes, _, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert themes == []
    assert schemes == []
    auto_writes = [command for command in writes if "AutomaticLookAndFeel" in command]
    assert auto_writes
    assert "--type" in auto_writes[0] and "bool" in auto_writes[0]
    interval_writes = [
        command for command in writes if "AutomaticLookAndFeelIdleInterval" in command
    ]
    assert interval_writes
    assert "99" in interval_writes[0]
    assert "--notify" in interval_writes[0]


def test_live_session_notifies_watched_files_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With a live desktop session the kwinrc and kdeglobals writes carry
    # the --notify flag so the running kwin applies them live; writes to
    # files nobody watches stay without it.
    ctx = _ctx(tmp_path, automatic_look_and_feel=1)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    watched = [
        command for command in writes if "kwinrc" in command or "kdeglobals" in command
    ]
    others = [
        command
        for command in writes
        if "kwinrc" not in command and "kdeglobals" not in command
    ]
    assert watched
    assert others
    assert all("--notify" in command for command in watched)
    assert all("--notify" not in command for command in others)


def test_no_session_omits_notify_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a desktop session there is no bus to notify, so the writes
    # carry no --notify flag and apply at the next login as before.
    ctx = _ctx(tmp_path, automatic_look_and_feel=1)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, bus_pid="")
    result = task_module.task(ctx)
    assert result.success is True
    assert writes
    assert all("--notify" not in command for command in writes)


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
        KconfigRecord(
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
    _ctx(tmp_path, system_look_and_feel_dir=system)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    changed = task_module._apply_theme_cursor_overrides(timeout=5, force=False)
    assert changed is True
    user_dir = tmp_path / ".local/share/plasma/look-and-feel"
    dark_defaults = user_dir / "org.kubuntudark.desktop/contents/defaults"
    light_defaults = user_dir / "org.kubuntulight.desktop/contents/defaults"
    assert dark_defaults.is_file()
    assert light_defaults.is_file()
    defaults_writes = [command for command in writes if "defaults" in " ".join(command)]
    assert any("Oxygen_Yellow" in command for command in defaults_writes)
    assert any("Oxygen_Blue" in command for command in defaults_writes)


def test_theme_cursor_overrides_skip_missing_system_themes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing system theme is not an error; the copy is skipped.
    _ctx(tmp_path)
    _install_fakes(monkeypatch)
    changed = task_module._apply_theme_cursor_overrides(timeout=5, force=False)
    assert changed is False


def test_theme_cursor_overrides_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A second pass with matching cursor values changes nothing.
    system = tmp_path / "system-look-and-feel"
    _make_system_theme(system, "org.kubuntudark.desktop")
    _make_system_theme(system, "org.kubuntulight.desktop")
    _ctx(tmp_path, system_look_and_feel_dir=system)
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
    changed = task_module._apply_theme_cursor_overrides(timeout=5, force=False)
    assert changed is True
    assert writes
    changed2 = task_module._apply_theme_cursor_overrides(timeout=5, force=False)
    assert changed2 is False


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
    _install_fakes(monkeypatch)
    changed = task_module._apply_user_dirs(timeout=5, force=False)
    assert changed is True
    text = (config_dir / "user-dirs.dirs").read_text(encoding="utf-8")
    assert f'XDG_MUSIC_DIR="{values.USER_DIRS["XDG_MUSIC_DIR"]}"' in text
    assert 'XDG_DESKTOP_DIR="$HOME/Desktop"' in text
    changed2 = task_module._apply_user_dirs(timeout=5, force=False)
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
    _install_fakes(monkeypatch)
    changed = task_module._apply_konsole_profile(asset, timeout=5, force=False)
    assert changed is True
    target = tmp_path / ".local/share/konsole/Pyntara.profile"
    expected = f"Directory={tmp_path}/Downloads/\nName=Pyntara\n"
    assert target.read_text(encoding="utf-8") == expected
    changed2 = task_module._apply_konsole_profile(asset, timeout=5, force=False)
    assert changed2 is False


def _script_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: bool = True,
    assign_calls: list[list[str]] | None = None,
    assign_state: dict[str, list[str]] | None = None,
    assign_after: dict[str, list[str]] | None = None,
) -> tuple[list[list[str]], list[list[str]]]:
    """Replace run_command for the KWin script install helpers.

    The fake answers the mkdir, kreadconfig6, kwriteconfig6, the system
    python3 and chown/chmod commands the install and hotkey steps run;
    writes and live releases are recorded. assign_calls collects the
    calls of the hotkey client, assign_state is the key state the daemon
    already holds and assign_after overrides the key state the client
    reports back.
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
                if _is_assign_call(inner):
                    return _assign_reply(
                        inner, assign_calls, assign_state, assign_after
                    )
                releases.append(list(command))
                return _FakeProc(0, "")
        if command[0] in ("chown", "chmod"):
            return _FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    monkeypatch.setattr(
        task_module,
        "session_environment",
        (
            lambda username, timeout: (
                {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
                if session
                else {}
            )
        ),
    )
    return writes, releases


def _write_script_templates(root: Path) -> None:
    """Write minimal KWin script templates under the given root."""

    for script in values.KWIN_SCRIPTS:
        for rel_file in values.KWIN_SCRIPT_FILES:
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
    _ctx(tmp_path)
    writes, _ = _script_fakes(monkeypatch)
    changed = task_module._apply_kwin_scripts(template_root, timeout=5, force=False)
    assert changed is True
    for script in values.KWIN_SCRIPTS:
        for rel_file in values.KWIN_SCRIPT_FILES:
            target = tmp_path / ".local/share/kwin/scripts" / script / rel_file
            assert target.read_text(encoding="utf-8") == rel_file
    enabled = [
        command[command.index("--key") + 1]
        for command in writes
        if "kwriteconfig6" in command and "kwinrc" in command
    ]
    for script in values.KWIN_SCRIPTS:
        assert f"{script}Enabled" in enabled
    changed2 = task_module._apply_kwin_scripts(template_root, timeout=5, force=False)
    assert changed2 is False


def test_apply_kwin_scripts_missing_templates_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No templates: the step changes nothing and is not an error.
    writes, _ = _script_fakes(monkeypatch)
    changed = task_module._apply_kwin_scripts(
        tmp_path / "missing", timeout=5, force=False
    )
    assert changed is False
    assert writes == []


def test_kwin_scripts_installed_and_the_records_written_without_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a live session the combinations cannot reach the running
    # daemon, so the full task installs the scripts and writes the
    # combinations into the shortcut file for the next login, clearing a
    # foreign action that holds a claimed key.
    template_root = tmp_path / "task_data" / "kde_settings" / "kwin"
    _write_script_templates(template_root)
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "kglobalshortcutsrc").write_text(
        "[kwin]\n"
        "Switch One Desktop Up=Meta+Ctrl+Up,Meta+Ctrl+Up,Switch One Desktop Up\n"
        "Switch One Desktop Down=Meta+Ctrl+Down,Meta+Ctrl+Down,Switch One Desktop Down\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path, repo_root=tmp_path)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, bus_pid="")
    result = task_module.task(ctx)
    assert result.success is True
    for script in values.KWIN_SCRIPTS:
        for rel_file in values.KWIN_SCRIPT_FILES:
            target = tmp_path / ".local/share/kwin/scripts" / script / rel_file
            assert target.read_text(encoding="utf-8") == rel_file
    cleared = [
        command
        for command in writes
        if "Switch One Desktop Up" in command or "Switch One Desktop Down" in command
    ]
    assert len(cleared) == 2


def test_script_hotkey_pairs_read_the_value_actions_and_hotkeys(
    tmp_path: Path,
) -> None:
    # The two lists of the section describe one hotkey per position, and the
    # shared client turns a combination into the combined key code the
    # daemon takes, so the task holds no table of hand written codes.
    assert task_module._script_hotkey_pairs() == (
        ("Grow Window by 5px", "Meta+Ctrl+Up"),
        ("Shrink Window by 5px", "Meta+Ctrl+Down"),
    )


def test_shortcut_record_changes_read_only_the_first_field(
    tmp_path: Path,
) -> None:
    # Every record of the shortcut file with the portable form becomes one
    # change, and only the first field is read: the second field is the
    # combination the action ships with, so reading it would take a key
    # the action must not own. The absent word and an empty field mean no
    # combination, and a record of another file stays with the plain
    # KConfig values.
    values.KCONFIG_RECORDS = _SHORTCUT_RECORDS
    assert task_module._shortcut_record_changes() == (
        ("kwin", "kwin", "Walk Through Windows", ("Alt+Tab",)),
        ("kwin", "kwin", "MinimizeAll", ("Meta+D",)),
        ("plasmashell", "plasmashell", "manage activities", ()),
    )


def _shortcut_env(ctx: Any) -> dict[str, str]:
    """The session environment of a live desktop for the shortcut client."""

    return {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}


def test_apply_shortcuts_live_runs_the_shared_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole intended state goes to the running daemon in one call: the
    # configured records first, then the combinations of the KWin script
    # actions, and the state the client reports back decides whether the
    # task changed anything.
    ctx = _ctx(tmp_path, kconfig=_SHORTCUT_RECORDS)
    calls: list[list[str]] = []
    _install_fakes(monkeypatch, assign_calls=calls)
    changed = task_module._apply_shortcuts_live(
        client_path=_shared_client(tmp_path),
        timeout=5,
        env=_shortcut_env(ctx),
        system_python=engine_values.SYSTEM_PYTHON,
        kglobalaccel_names=kglobalaccel_names(),
    )
    assert changed is True
    assert len(calls) == 1
    assert calls[0][0] == engine_values.SYSTEM_PYTHON
    client_path = Path(calls[0][1])
    assert client_path.name.endswith(engine_values.RENDERED_CLIENT_SUFFIX)
    assert client_path != _SHARED_CLIENT
    client_text = client_path.read_text(encoding="utf-8")
    assert engine_values.KGLOBALACCEL_BUS_NAME in client_text
    assert "$kglobalaccel_bus_name" not in client_text
    assert all("\n" not in part for part in calls[0])
    request = json.loads(calls[0][-1])
    assert request["changes"] == [
        {
            "component_unique": "kwin",
            "component_friendly": "kwin",
            "action": "Walk Through Windows",
            "keys": ["Alt+Tab"],
        },
        {
            "component_unique": "kwin",
            "component_friendly": "kwin",
            "action": "MinimizeAll",
            "keys": ["Meta+D"],
        },
        {
            "component_unique": "plasmashell",
            "component_friendly": "plasmashell",
            "action": "manage activities",
            "keys": [],
        },
        {
            "component_unique": values.KWIN_COMPONENT_UNIQUE,
            "component_friendly": values.KWIN_COMPONENT_FRIENDLY,
            "action": "Grow Window by 5px",
            "keys": ["Meta+Ctrl+Up"],
        },
        {
            "component_unique": values.KWIN_COMPONENT_UNIQUE,
            "component_friendly": values.KWIN_COMPONENT_FRIENDLY,
            "action": "Shrink Window by 5px",
            "keys": ["Meta+Ctrl+Down"],
        },
    ]


def test_apply_shortcuts_live_asks_again_until_the_state_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The state right after one attempt can still belong to another action,
    # so a state that is not the configured one is asked for again, with
    # the configured pause between two attempts.
    ctx = _ctx(tmp_path, kconfig=_SHORTCUT_RECORDS)
    calls: list[list[str]] = []
    pauses: list[float] = []
    monkeypatch.setattr(task_module.time, "sleep", pauses.append)
    _install_fakes(
        monkeypatch,
        assign_calls=calls,
        assign_after_sequence=[
            {"Walk Through Windows": ["Meta+Tab"]},
            {},
        ],
    )
    changed = task_module._apply_shortcuts_live(
        client_path=_shared_client(tmp_path),
        timeout=5,
        env=_shortcut_env(ctx),
        system_python=engine_values.SYSTEM_PYTHON,
        kglobalaccel_names=kglobalaccel_names(),
    )
    assert changed is True
    assert len(calls) == 2
    assert pauses == [values.SHORTCUT_APPLY_RETRY_DELAY_SECONDS]


def test_apply_shortcuts_live_stops_at_once_for_a_combination_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A combination the client cannot read stays unreachable whatever the
    # number of attempts, so that change is reported at once, without
    # waiting, and its record is written for the next login.
    ctx = _ctx(tmp_path, kconfig=_SHORTCUT_RECORDS)
    calls: list[list[str]] = []
    messages: list[str] = []
    pauses: list[float] = []
    monkeypatch.setattr(task_module.time, "sleep", pauses.append)
    monkeypatch.setattr(task_module, "_log", messages.append)
    _, _, _, _, writes, _, _ = _install_fakes(
        monkeypatch,
        assign_calls=calls,
        assign_unsupported={"MinimizeAll": ["meta+u"]},
    )
    task_module._apply_shortcuts_live(
        client_path=_shared_client(tmp_path),
        timeout=5,
        env=_shortcut_env(ctx),
        system_python=engine_values.SYSTEM_PYTHON,
        kglobalaccel_names=kglobalaccel_names(),
        warnings=[],
    )
    assert len(calls) == 1
    assert pauses == []
    assert any("cannot read meta+u" in message for message in messages)
    written = {command[command.index("--key") + 1]: command[-1] for command in writes}
    assert set(written) == {"MinimizeAll"}
    assert written["MinimizeAll"] == "Meta+D,none,MinimizeAll"


def test_apply_shortcuts_live_assigns_an_action_the_daemon_does_not_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The daemon lists no per-layout action of the keyboard layout switcher,
    # and the client applies it anyway: measured on Kubuntu 26.04, the daemon
    # stores the combination of an action it does not list, and kwin reads it
    # when it starts. Nothing is refused, so nothing is written for a later
    # login and the task reports the work it did.
    ctx = _ctx(tmp_path, kconfig=_SHORTCUT_RECORDS)
    calls: list[list[str]] = []
    warnings: list[str] = []
    monkeypatch.setattr(task_module.time, "sleep", lambda _seconds: None)
    _, _, _, _, writes, _, _ = _install_fakes(
        monkeypatch,
        assign_calls=calls,
        assign_missing=frozenset({"manage activities"}),
    )
    changed = task_module._apply_shortcuts_live(
        client_path=_shared_client(tmp_path),
        timeout=5,
        env=_shortcut_env(ctx),
        system_python=engine_values.SYSTEM_PYTHON,
        kglobalaccel_names=kglobalaccel_names(),
        warnings=warnings,
    )
    assert changed is True
    assert len(calls) == 1
    assert warnings == []
    assert writes == []


def test_the_shortcut_warning_names_the_component_the_action_and_the_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A combination that stays with another action is a refusal, and the
    # warning names the component, the action, the state and the action that
    # kept the combination, so a user reads what to act on instead of a list
    # of key codes and a promise about the next login.
    ctx = _ctx(tmp_path, kconfig=_SHORTCUT_RECORDS)
    warnings: list[str] = []
    monkeypatch.setattr(task_module.time, "sleep", lambda _seconds: None)
    _install_fakes(
        monkeypatch,
        assign_after={"MinimizeAll": []},
        assign_held_elsewhere={
            "MinimizeAll": [[285212672, "kwin", "Cube", "KWin", "Toggle Cube"]]
        },
    )
    task_module._apply_shortcuts_live(
        client_path=_shared_client(tmp_path),
        timeout=5,
        env=_shortcut_env(ctx),
        system_python=engine_values.SYSTEM_PYTHON,
        kglobalaccel_names=kglobalaccel_names(),
        warnings=warnings,
    )
    assert len(warnings) == 1
    message = warnings[0]
    assert "kwin (kwin), action MinimizeAll" in message
    assert "asked for Meta+D" in message
    assert "the action holds nothing" in message
    assert "it stays with KWin (kwin), action Cube" in message


def test_apply_shortcuts_live_warns_and_writes_what_the_daemon_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A combination the daemon does not report back after the configured
    # number of attempts is a warning of a completed task, and the record
    # of the action is written for the next login with the fields the
    # daemon owns kept exactly as the file has them.
    ctx = _ctx(tmp_path, kconfig=_SHORTCUT_RECORDS)
    calls: list[list[str]] = []
    warnings: list[str] = []
    monkeypatch.setattr(task_module.time, "sleep", lambda _seconds: None)
    _, _, _, _, writes, _, _ = _install_fakes(
        monkeypatch,
        currents={"MinimizeAll": "none,Meta+D,Minimize all windows"},
        assign_calls=calls,
        assign_after={"MinimizeAll": []},
    )
    changed = task_module._apply_shortcuts_live(
        client_path=_shared_client(tmp_path),
        timeout=5,
        env=_shortcut_env(ctx),
        system_python=engine_values.SYSTEM_PYTHON,
        kglobalaccel_names=kglobalaccel_names(),
        warnings=warnings,
    )
    assert changed is True
    assert len(calls) == values.SHORTCUT_APPLY_ATTEMPTS
    assert len(warnings) == 1
    assert "MinimizeAll" in warnings[0]
    written = {command[command.index("--key") + 1]: command[-1] for command in writes}
    assert written["MinimizeAll"] == "Meta+D,Meta+D,Minimize all windows"
    assert "Walk Through Windows" not in written


def test_apply_shortcuts_live_clears_a_foreign_record_holding_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a live session the combinations cannot reach the running
    # daemon, so the file carries them to the next login; a foreign record
    # that holds one of the keys loses it there, so the next login does
    # not hand the key to another action first.
    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "kglobalshortcutsrc").write_text(
        "[kwin]\n"
        "Switch One Desktop Up=Meta+Ctrl+Up,Meta+Ctrl+Up,Switch One Desktop Up\n"
        "[kwin]\n"
        "Walk Through Windows=Alt+Tab,Meta+Tab<TAB>Alt+Tab,Walk Through Windows\n",
        encoding="utf-8",
    )
    _ctx(tmp_path, kconfig=_SHORTCUT_RECORDS)
    calls: list[list[str]] = []
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, assign_calls=calls)
    changed = task_module._apply_shortcuts_live(
        client_path=_shared_client(tmp_path),
        timeout=5,
        env=None,
        system_python=engine_values.SYSTEM_PYTHON,
        kglobalaccel_names=kglobalaccel_names(),
        warnings=[],
    )
    assert changed is False
    assert calls == []
    written = {command[command.index("--key") + 1]: command[-1] for command in writes}
    assert written["Switch One Desktop Up"] == "none,Meta+Ctrl+Up,Switch One Desktop Up"
    assert written["Walk Through Windows"] == "Alt+Tab,none,Walk Through Windows"


def test_write_script_hotkey_records_repairs_a_refused_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A record an earlier run left without an active key wins over the
    # combination the script registers, so the task rewrites it in the
    # shape of a granted hotkey and keeps its description.
    description = "Grow the active window by 5 pixels on each side"
    currents = {"Grow Window by 5px": f",none,{description}"}
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, currents=currents)
    changed = task_module._write_script_hotkey_records(
        timeout=5, force=False, warnings=[]
    )
    assert changed is True
    written = [command for command in writes if "Grow Window by 5px" in command]
    assert written
    assert written[0][-1] == f"Meta+Ctrl+Up,none,{description}"


def test_write_script_hotkey_records_leave_an_absent_record_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An action without a record gets its record from the script when it
    # registers at login, so the task writes nothing and changes nothing.
    matched = {"Grow Window by 5px": "Meta+Ctrl+Up,none,Grow Window by 5px"}
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, currents=matched)
    changed = task_module._write_script_hotkey_records(
        timeout=5, force=False, warnings=[]
    )
    assert changed is False
    assert writes == []


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


def test_notify_flag_follows_the_value_file_names() -> None:
    # Only the files the values name carry a live watcher, so a renamed file
    # in the values is the file the flag is added for.
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
    assert task_module._notify_flag(values.KWINRC_FILE_NAME, env) == ["--notify"]
    values.KWINRC_FILE_NAME = "kwinrc-custom"
    assert task_module._notify_flag("kwinrc", env) == []
    assert task_module._notify_flag("kwinrc-custom", env) == ["--notify"]


def test_places_namespace_address_comes_from_the_values() -> None:
    # Another address in the values is the address the task declares, so the
    # namespace of the file is a value and not a literal in the code.
    values.PLACES_NAMESPACES = {"bookmark": "http://example.invalid/bookmarks"}
    declared = task_module._declare_missing_prefixes("<xbel>")
    assert 'xmlns:bookmark="http://example.invalid/bookmarks"' in declared


def test_places_metadata_owner_comes_from_the_values() -> None:
    # Only a metadata block with the configured owner may be hidden: with
    # another owner in the values the same file is left unchanged.
    values.PLACES_METADATA_OWNER = "http://example.invalid/owner"
    assert task_module._places_xbel_hidden(XBEL, {"Home"}) is None


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
    values.PLACES_HIDDEN = ("Home",)
    _install_fakes(monkeypatch)
    changed = task_module._apply_places_hidden(timeout=5, force=False)
    assert changed is True
    text = (places_dir / "user-places.xbel").read_text(encoding="utf-8")
    assert "IsHidden" in text
    changed2 = task_module._apply_places_hidden(timeout=5, force=False)
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
    values.PLACES_HIDDEN = ("Home",)
    _install_fakes(monkeypatch)
    changed = task_module._apply_places_hidden(timeout=5, force=False)
    assert changed is False


def test_apply_places_hidden_missing_file_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without the Places file the hiding is skipped and not an error.
    values.PLACES_HIDDEN = ("Home",)
    _install_fakes(monkeypatch)
    changed = task_module._apply_places_hidden(timeout=5, force=False)
    assert changed is False


def test_places_xbel_hidden_tolerates_undeclared_bookmark_prefix() -> None:
    # A Dolphin file can bind the icon namespace as ns0 while still using
    # the bookmark: prefix undeclared; the tolerant parse still hides the
    # matched place and serializes a well-formed file.
    malformed = XBEL.replace(
        'xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks"',
        'xmlns:ns0="http://www.freedesktop.org/standards/desktop-bookmarks"',
    )
    out = task_module._places_xbel_hidden(malformed, {"Home"})
    assert out is not None
    home = out.split("<title>Home</title>")[1].split("</bookmark>")[0]
    assert "<IsHidden>true</IsHidden>" in home
    assert 'xmlns:bookmark="http://freedesktop.org/standards/desktop-bookmarks"' in out


def test_apply_places_hidden_unparseable_file_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Places file that even the tolerant parse cannot read skips the
    # hiding with a message instead of failing the whole task.
    places_dir = tmp_path / ".local/share"
    places_dir.mkdir(parents=True, exist_ok=True)
    (places_dir / "user-places.xbel").write_text("<xbel><bookmark>", encoding="utf-8")
    values.PLACES_HIDDEN = ("Home",)
    _install_fakes(monkeypatch)
    changed = task_module._apply_places_hidden(timeout=5, force=False)
    assert changed is False


def test_touchpad_clickareas_writes_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The clickareas method maps to the ClickMethod value 2.
    values.TOUCHPAD_CLICK_METHOD = "clickareas"
    ctx = make_context(
        install_mode="desktop",
        task_data_root=tmp_path,
        repo_root=_temporary_clone(tmp_path),
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
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch)
    changed = task_module._apply_sddm(timeout=5, force=False)
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
    currents = {
        "User": "i",
        "Session": "plasma",
        "Current": "kubuntu",
        "CursorSize": "30",
        "CursorTheme": "breeze_cursors",
        "Font": "Noto Sans,20",
    }
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, currents=currents)
    changed = task_module._apply_sddm(timeout=5, force=False)
    assert changed is False
    assert writes == []


def _kconfig_ctx(
    tmp_path: Path,
    records: tuple[KconfigRecord, ...],
    *,
    force: bool = False,
):
    """Context whose kconfig list carries the given records and whose theme
    state is the fixed dark theme, so an idempotent run has nothing to do."""

    values.AUTOMATIC_LOOK_AND_FEEL = 0
    values.SYSTEM_LOOK_AND_FEEL_DIR = tmp_path / "no-system-themes"
    values.KCONFIG_RECORDS = records
    return make_context(
        task_name="kde_settings",
        install_mode="desktop",
        force_tasks=frozenset({"kde_settings"}) if force else frozenset(),
        task_data_root=tmp_path,
        repo_root=_temporary_clone(tmp_path),
    )


def _preconfigure_user_files(tmp_path: Path) -> None:
    """Write the user-dirs.dirs and the Konsole profile the task expects,
    so an idempotent run sees them as already configured."""

    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "user-dirs.dirs").write_text(
        task_module._user_dirs_merged("", values.USER_DIRS), encoding="utf-8"
    )
    profile = (_REPO_ROOT / "task_data" / "kde_settings" / "Pyntara.profile").read_text(
        encoding="utf-8"
    )
    profile = profile.replace("{home_dir}", common_values.DESKTOP_HOME_DIR)
    profile_dir = tmp_path / ".local/share/konsole"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "Pyntara.profile").write_text(profile, encoding="utf-8")
    for script in values.KWIN_SCRIPTS:
        for rel_file in values.KWIN_SCRIPT_FILES:
            template = (
                _REPO_ROOT / "task_data" / "kde_settings" / "kwin" / script / rel_file
            )
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
        KconfigRecord(
            "kwinrc", ("TabBox",), "LayoutName", "coverswitch", "string", False
        ),
        KconfigRecord("kdeglobals", ("KDE",), "SingleClick", "true", "bool", False),
        KconfigRecord("kwinrc", ("TabBox",), "StaleKey", "", "string", True),
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
        command for command in writes if "StaleKey" in command and "--delete" in command
    ]
    assert layout_writes
    assert "coverswitch" in layout_writes[0]
    assert "--notify" in layout_writes[0]
    assert single_writes
    assert "--type" in single_writes[0] and "bool" in single_writes[0]
    assert delete_writes


def test_the_bool_type_word_comes_from_the_config_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The word that marks a boolean record is the name the values module
    # gives that type: with another name in the vocabulary the task still
    # follows it, so the task and the checks that validate a record
    # against KCONFIG_TYPES cannot drift apart.
    records = (
        KconfigRecord("kdeglobals", ("KDE",), "SingleClick", "true", "bool", False),
    )
    ctx = _kconfig_ctx(tmp_path, records)
    _, _, _, _, writes, _, _ = _install_fakes(monkeypatch, currents={})
    monkeypatch.setattr(values, "KCONFIG_BOOL_TYPE", "flag")
    result = task_module.task(ctx)
    assert result.success is True
    single_writes = [command for command in writes if "SingleClick" in command]
    assert single_writes
    assert "--type" not in single_writes[0]


def test_kconfig_records_skip_when_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A value record whose key already matches skips the write; a delete
    # record whose key is absent skips the deletion, so nothing changes.
    records = (
        KconfigRecord(
            "kwinrc", ("TabBox",), "LayoutName", "coverswitch", "string", False
        ),
        KconfigRecord("kwinrc", ("TabBox",), "StaleKey", "", "string", True),
    )
    ctx = _kconfig_ctx(tmp_path, records)
    currents = dict(FULLY_CONFIGURED, LayoutName="coverswitch")
    _preconfigure_user_files(tmp_path)
    _, _, _, _, writes, _, _ = _install_fakes(
        monkeypatch,
        currents=currents,
        assign_state=_granted_script_hotkeys(),
    )
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
        KconfigRecord(
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


def _is_desktop_count_call(command: list[str]) -> bool:
    """True for the call that reads the live number of desktops.

    The number is a property of the desktop interface, not a call, so the
    command goes through the property reader of DBus and a test
    recognises it by that method name.
    """

    return any(part.endswith(".Get") for part in command)


def _write_desktop_ids_client(tmp_path: Path) -> Path:
    """Write the python desktop id client the task runs, return its path.

    The task reads the client text from the file the config names, renders it
    next to that file and passes the path to the interpreter, so the test
    hands over a file of its own and reads back what the task rendered.
    """

    path = tmp_path / "list_desktop_ids.py"
    path.write_text("import dbus\nprint('id')\n", encoding="utf-8")
    return path


def _rendered_client_path(command: list[str]) -> Path | None:
    """The rendered client a command runs, or None for another command."""

    for part in command:
        if part.endswith(f".{engine_values.RENDERED_CLIENT_SUFFIX}"):
            return Path(part)
    return None


def test_desktop_count_live_removes_extra_desktops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live count is higher than the configured Number: the task reads
    # the desktop ids through the python3-dbus client shipped as task data
    # and removes the trailing extras.
    records = (KconfigRecord("kwinrc", ("Desktops",), "Number", "4", "string", False),)
    _kconfig_ctx(tmp_path, records)
    _write_desktop_ids_client(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        joined = " ".join(command)
        if _is_desktop_count_call(command):
            return _FakeProc(0, "6")
        if _rendered_client_path(command) is not None:
            return _FakeProc(0, "id1\nid2\nid3\nid4\nid5\nid6\n")
        if "removeDesktop" in joined:
            return _FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    env = {
        "HOME": str(tmp_path),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }
    error = task_module._apply_desktop_count_live(
        script_path=_write_desktop_ids_client(tmp_path),
        timeout=30.0,
        env=env,
        system_python=engine_values.SYSTEM_PYTHON,
    )
    assert error is None
    removals = [command for command in calls if "removeDesktop" in " ".join(command)]
    removed_ids = []
    for command in removals:
        method_index = next(
            index
            for index, part in enumerate(command)
            if part.endswith("removeDesktop")
        )
        removed_ids.append(command[method_index + 1])
    assert removed_ids == ["id5", "id6"]


def test_the_desktop_dbus_names_come_from_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bus name, the object path and the interface of the KWin virtual
    # desktop manager are declared values of the section: another vocabulary
    # is what the commands carry and what the desktop list client receives,
    # and the shipped names stop appearing.
    records = (KconfigRecord("kwinrc", ("Desktops",), "Number", "2", "string", False),)
    _kconfig_ctx(tmp_path, records)
    values.KWIN_BUS_NAME = "org.example.KWin"
    values.VIRTUAL_DESKTOP_MANAGER_OBJECT_PATH = "/ExampleDesktopManager"
    values.VIRTUAL_DESKTOP_MANAGER_INTERFACE_NAME = "org.example.DesktopManager"
    values.VIRTUAL_DESKTOPS_PROPERTY_NAME = "screens"
    values.DBUS_PROPERTIES_INTERFACE_NAME = "org.example.Properties"
    calls: list[list[str]] = []
    client_texts: list[str] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        joined = " ".join(command)
        if _is_desktop_count_call(command):
            return _FakeProc(0, "4")
        rendered_client = _rendered_client_path(command)
        if rendered_client is not None:
            client_texts.append(rendered_client.read_text(encoding="utf-8"))
            return _FakeProc(0, "id1\nid2\nid3\nid4\n")
        if "removeDesktop" in joined:
            return _FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    env = {
        "HOME": str(tmp_path),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }
    shipped_client = (
        Path(__file__).resolve().parents[1]
        / "task_data"
        / "kde_settings"
        / "list_desktop_ids.py"
    )
    client_path = tmp_path / "list_desktop_ids.py"
    client_path.write_text(shipped_client.read_text(encoding="utf-8"), encoding="utf-8")
    error = task_module._apply_desktop_count_live(
        script_path=client_path,
        timeout=30.0,
        env=env,
        system_python=engine_values.SYSTEM_PYTHON,
    )
    assert error is None
    assert any("org.example.KWin" in part for part in calls[0])
    assert all("$kwin_bus_name" not in part for command in calls for part in command)
    assert all(
        "$virtual_desktop_count_property_name" not in part
        for command in calls
        for part in command
    )
    assert client_texts
    assert "org.example.KWin" in client_texts[0]
    assert "org.example.Properties" in client_texts[0]
    assert "screens" in client_texts[0]
    assert "$virtual_desktops_property_name" not in client_texts[0]


def test_desktop_count_live_creates_missing_desktops_at_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live count is lower than the configured Number: the task creates
    # the missing desktops at the end, so existing ones keep their place.
    records = (KconfigRecord("kwinrc", ("Desktops",), "Number", "5", "string", False),)
    _kconfig_ctx(tmp_path, records)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        joined = " ".join(command)
        if _is_desktop_count_call(command):
            return _FakeProc(0, "3")
        if "createDesktop" in joined:
            return _FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    env = {
        "HOME": str(tmp_path),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }
    error = task_module._apply_desktop_count_live(
        script_path=_write_desktop_ids_client(tmp_path),
        timeout=30.0,
        env=env,
        system_python=engine_values.SYSTEM_PYTHON,
    )
    assert error is None
    creates = [command for command in calls if "createDesktop" in " ".join(command)]
    positions = []
    for command in creates:
        method_index = next(
            index
            for index, part in enumerate(command)
            if part.endswith("createDesktop")
        )
        positions.append(command[method_index + 1])
    assert positions == ["3", "4"]


def test_desktop_count_live_reports_a_missing_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing task data file is reported with its path before anything is
    # removed, so the run never deletes desktops without its id list.
    records = (KconfigRecord("kwinrc", ("Desktops",), "Number", "4", "string", False),)
    _kconfig_ctx(tmp_path, records)
    missing_client = tmp_path / "missing_desktop_ids.py"
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        if _is_desktop_count_call(command):
            return _FakeProc(0, "6")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    env = {
        "HOME": str(tmp_path),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }
    error = task_module._apply_desktop_count_live(
        script_path=missing_client,
        timeout=30.0,
        env=env,
        system_python=engine_values.SYSTEM_PYTHON,
    )
    assert error is not None
    assert str(missing_client) in error
    assert not [command for command in calls if "removeDesktop" in " ".join(command)]


def test_kconfig_record_failure_keeps_other_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One record whose write fails is reported, the remaining records
    # still apply, and the task completes as done with warnings.
    records = (
        KconfigRecord(
            "kwinrc", ("TabBox",), "LayoutName", "coverswitch", "string", False
        ),
        KconfigRecord("kdeglobals", ("KDE",), "SingleClick", "true", "bool", False),
    )
    ctx = _kconfig_ctx(tmp_path, records)
    _, _, _, _, writes, _, _ = _install_fakes(
        monkeypatch, fail_on_write_keys=frozenset({"LayoutName"})
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert any("LayoutName" in warning for warning in result.warnings)
    single_writes = [command for command in writes if "SingleClick" in command]
    assert single_writes


def test_sddm_one_key_failure_keeps_other_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One system key whose write fails is reported and the remaining SDDM
    # keys still apply.
    writes: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        if command[0] == "kreadconfig6":
            return _FakeProc(0, "")
        if command[0] == "kwriteconfig6":
            key = command[command.index("--key") + 1]
            if key == "CursorSize":
                raise subprocess.CalledProcessError(1, command)
            writes.append(list(command))
            return _FakeProc(0, "")
        if command[0] in ("chown", "chmod"):
            return _FakeProc(0, "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    warnings: list[str] = []
    changed = task_module._apply_sddm(timeout=5, force=False, warnings=warnings)
    assert changed is True
    assert any("CursorSize" in warning for warning in warnings)
    assert any("User" in command for command in writes)
    assert any("Font" in command for command in writes)
    assert not any("CursorSize" in command for command in writes)


def test_user_command_prefix_comes_from_the_values() -> None:
    # The wrapper that runs a command as the desktop user is a value:
    # another wrapper in the values is the argv the task builds.
    values.RUNUSER_COMMAND = ("sudo", "-u", "{username}", "--")
    assert task_module._as_user_command(["kwriteconfig6", "--file", "kwinrc"]) == [
        "sudo",
        "-u",
        common_values.DESKTOP_USERNAME,
        "--",
        "kwriteconfig6",
        "--file",
        "kwinrc",
    ]


def test_plasma_apply_calls_come_from_the_values() -> None:
    # The three appearance tools and their flags are values: another call in
    # the values is the argv the task runs, with the value it applies
    # substituted.
    values.APPLY_LOOK_AND_FEEL_COMMAND = ("my-theme", "-a", "{look_and_feel}")
    assert task_module._appearance_command("look_and_feel") == [
        "my-theme",
        "-a",
        values.LOOK_AND_FEEL,
    ]
    values.APPLY_COLOR_SCHEME_COMMAND = (
        "my-scheme",
        "--set",
        "{color_scheme}",
    )
    assert task_module._appearance_command("color_scheme") == [
        "my-scheme",
        "--set",
        values.COLOR_SCHEME,
    ]
    assert task_module._appearance_command("cursor_theme") == [
        "plasma-apply-cursortheme",
        values.CURSOR_THEME,
    ]


def test_kconfig_calls_come_from_the_values() -> None:
    # The two base calls and the three selectors are values: another set of
    # them is what the task builds, for the user session and for the system
    # files.
    values.KREADCONFIG_COMMAND = ("my-reader", "--config", "{file_name}")
    values.CONFIG_GROUP_FLAG = ("--section", "{group}")
    values.CONFIG_KEY_FLAG = ("--entry", "{key}")
    expected = [
        "my-reader",
        "--config",
        "kwinrc",
        "--section",
        "Group",
        "--section",
        "Sub",
        "--entry",
        "Key",
    ]
    assert (
        task_module._kconfig_command(
            values.KREADCONFIG_COMMAND, "kwinrc", ("Group", "Sub"), "Key"
        )
        == expected
    )
    values.KWRITECONFIG_COMMAND = ("my-writer", "--config", "{file_name}")
    assert task_module._kconfig_command(
        values.KWRITECONFIG_COMMAND, "kdeglobals", ("Group",), "Key"
    ) == [
        "my-writer",
        "--config",
        "kdeglobals",
        "--section",
        "Group",
        "--entry",
        "Key",
    ]


def test_file_operations_come_from_the_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The maker of the parent directory, the owner writer and the mode
    # writer are values: another program in the values is the argv the task
    # runs around a user config file.
    values.MKDIR_COMMAND = ("mymkdir", "--parents", "{path}")
    values.CHOWN_COMMAND = ("mychown", "--owner", "{owner}", "{path}")
    values.CHMOD_COMMAND = ("mychmod", "--mode", "{file_mode}", "{path}")
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        seen.append(list(command))
        return _FakeProc(0, "")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    assert task_module._write_user_file(
        ".config/kxkbrc",
        "body\n",
        mode=0o600,
        timeout=30.0,
        force=True,
    )
    target = tmp_path / ".config" / "kxkbrc"
    assert seen[0][4:] == ["mymkdir", "--parents", str(target.parent)]
    assert seen[1] == [
        "mychown",
        "--owner",
        (f"{common_values.DESKTOP_USERNAME}:{common_values.DESKTOP_USERNAME}"),
        str(target),
    ]
    assert seen[2] == ["mychmod", "--mode", f"{0o600:04o}", str(target)]


def test_recursive_owner_command_comes_from_the_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The owner writer of a copied theme tree is a value: another program in
    # the values is the argv the task runs on the copy.
    system = tmp_path / "system"
    values.SYSTEM_LOOK_AND_FEEL_DIR = system
    values.CHOWN_RECURSIVE_COMMAND = (
        "mychown",
        "--recursive",
        "{owner}",
        "{path}",
    )
    (system / values.LOOK_AND_FEEL).mkdir(parents=True)
    target = (
        Path(common_values.DESKTOP_HOME_DIR)
        / values.USER_LOOK_AND_FEEL_DIR
        / values.LOOK_AND_FEEL
    )
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        seen.append(list(command))
        return _FakeProc(0, "")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    task_module._apply_theme_cursor_overrides(timeout=30.0, force=True, warnings=[])
    assert [
        "mychown",
        "--recursive",
        (f"{common_values.DESKTOP_USERNAME}:{common_values.DESKTOP_USERNAME}"),
        str(target),
    ] in seen


def test_substituted_record_fills_the_home_of_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A record that names a path of the desktop account carries the
    # placeholder, because the values module builds the record before the
    # engine resolves the account of the machine, and the task fills the
    # placeholder into the group and the value when it reads or writes it.
    home = str(tmp_path / "home")
    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", home)
    record = values.KconfigRecord(
        "ktrashrc",
        (f"{{{values.HOME_PLACEHOLDER_NAME}}}/.local/share/Trash",),
        "Days",
        "211",
    )
    substituted = task_module._substituted_record(record)
    assert substituted.group == (f"{home}/.local/share/Trash",)
    assert substituted.value == "211"
    assert substituted.key == record.key
    # A record that carries no placeholder is handed back unchanged, so the
    # comparison of its value stays a plain string comparison.
    plain = values.KconfigRecord("kwinrc", ("Windows",), "BorderSnapZone", "32")
    assert task_module._substituted_record(plain) is plain


def test_no_configured_record_carries_a_literal_home() -> None:
    # A literal home in the values names the machine that recorded them, so
    # a record that names a path of the desktop account carries the
    # placeholder and the target machine gets its own path.
    for record in values.KCONFIG_RECORDS:
        literal_parts = [
            text
            for text in (*record.group, record.value)
            if "/home/" in text and "{" not in text
        ]
        assert literal_parts == []


def test_the_activity_switcher_record_names_its_owning_component() -> None:
    # The Activity Switcher action belongs to plasmashell, and kwin carries
    # no action of that name: a kwin record would name an action the running
    # daemon does not know, so it would never apply and would be reported as
    # an unconfirmed shortcut on every run. The shipped records therefore
    # name the action in plasmashell alone.
    groups = [
        record.group
        for record in values.KCONFIG_RECORDS
        if record.file == "kglobalshortcutsrc"
        and record.key == "manage activities"
    ]
    assert groups == [("plasmashell",)]
