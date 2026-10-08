"""Unit tests for the firefox_setup task.

External commands (curl, apt-get, snap, git, pgrep, runuser, kreadconfig6,
kwriteconfig6) are mocked by monkeypatching run_command in both the task module
and the utils module, because the shared helpers of utils (the apt index refresh)
call their own run_command; the file operations run against the temporary tree,
because one autouse fixture points every writable path of the section at the
directory of the test (docs/guides/developer-guide.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import FakeProc, make_context

from pyntara import plasma_panel, task_catalog
from pyntara.context import Context
from pyntara.tasks import firefox_setup
from pyntara.values import common as common_values
from pyntara.values import firefox_setup as values
from pyntara.values import tasks as tasks_values

REAL_TASKS = tasks_values.CATALOG

POLICY_CONTENT = b'{"policies": {"SearchEngines": {"Default": "DuckDuckGo"}}}\n'
AUTOCONFIG_CONTENT = b'pref("general.config.filename", "mozilla.cfg");\n'
DEFAULTS_CONTENT = b'defaultPref("browser.uidensity", 1);\n'
KEY_CONTENT = (
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n-----END PGP PUBLIC KEY BLOCK-----\n"
)
APPLETSRC_TEXT = (
    "[Containments][2]\n"
    "plugin=org.kde.panel\n"
    "\n"
    "[Containments][2][Applets][5]\n"
    "plugin=org.kde.plasma.icontasks\n"
    "\n"
    "[Containments][2][Applets][5][Configuration][General]\n"
    "launchers=applications:org.kde.dolphin.desktop\n"
)


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test its own writable tree for the section values."""

    monkeypatch.setattr(values, "SETTINGS_DIR", tmp_path / "repo")
    monkeypatch.setattr(values, "SYSTEM_ROOT", tmp_path / "root")
    monkeypatch.setattr(
        values,
        "APT_SOURCE_PATH",
        tmp_path / "etc" / "apt" / "sources.list.d" / "mozilla.sources",
    )
    monkeypatch.setattr(
        values,
        "APT_PREFERENCES_PATH",
        tmp_path / "etc" / "apt" / "preferences.d" / "mozilla",
    )
    monkeypatch.setattr(
        values,
        "KEYRING_PATH",
        tmp_path / "usr" / "share" / "keyrings" / "packages.mozilla.org.asc",
    )
    monkeypatch.setattr(
        values,
        "BROWSER_BINARY_PATH",
        tmp_path / "usr" / "lib" / "firefox" / "firefox",
    )
    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(tmp_path / "home"))


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    return make_context(
        task_name="firefox_setup",
        install_mode="desktop",
        force_tasks=frozenset({"firefox_setup"}) if force else frozenset(),
    )


def _write_repository() -> None:
    """Create the defaults repository as if already cloned."""

    root = values.SETTINGS_DIR
    (root / ".git").mkdir(parents=True, exist_ok=True)
    system = root / values.SETTINGS_SYSTEM_TREE_RELATIVE_PATH
    files = {
        "usr/lib/firefox/distribution/policies.json": POLICY_CONTENT,
        "usr/lib/firefox/defaults/pref/autoconfig.js": AUTOCONFIG_CONTENT,
        "usr/lib/firefox/mozilla.cfg": DEFAULTS_CONTENT,
    }
    for relative, data in files.items():
        path = system / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "LICENSE").write_bytes(b"MIT\n")


def _mark_browser_installed() -> None:
    """Create the binary of the real browser build."""

    values.BROWSER_BINARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    values.BROWSER_BINARY_PATH.write_bytes(b"#!/bin/sh\n")


def _write_appletsrc() -> None:
    """Create the Plasma appletsrc of the desktop user."""

    path = Path(common_values.DESKTOP_HOME_DIR) / common_values.APPLETSRC_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(APPLETSRC_TEXT, encoding="utf-8")


def _kconfig_key(command: list[str]) -> tuple[str, tuple[str, ...], str]:
    """The (file, groups, key) an applet or KConfig command addresses."""

    file_name = ""
    groups: list[str] = []
    key = ""
    index = 0
    while index < len(command):
        if command[index] == "--file" and index + 1 < len(command):
            file_name = command[index + 1]
        elif command[index] == "--group" and index + 1 < len(command):
            groups.append(command[index + 1])
        elif command[index] == "--key" and index + 1 < len(command):
            key = command[index + 1]
        index += 1
    return file_name, tuple(groups), key


def _patch_run(monkeypatch: pytest.MonkeyPatch, fake_run: object) -> None:
    """Patch run_command in the task, the shared modules the task calls."""

    monkeypatch.setattr(firefox_setup, "run_command", fake_run)
    monkeypatch.setattr("pyntara.utils.run_command", fake_run)
    monkeypatch.setattr("pyntara.kconfig.run_command", fake_run)
    monkeypatch.setattr("pyntara.plasma_panel.run_command", fake_run)


def _is_curl(command: list[str]) -> bool:
    return bool(command) and command[0].endswith("curl")


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: bool = True,
    snap_present: bool = False,
    install_creates_binary: bool = True,
    calls: list[list[str]] | None = None,
) -> dict[tuple[str, tuple[str, ...], str], str]:
    """Replace run_command with a stand-in that answers by argv content.

    The KConfig calls share one in-memory store, so a value written by
    kwriteconfig6 is read back by kreadconfig6 and a second run observes the
    state the first one left, which is how the idempotency of the default-browser
    step is tested. The apt install writes the browser binary unless the test
    asks for a machine that still has no browser after the install.
    """

    recorded = calls if calls is not None else []
    store: dict[tuple[str, tuple[str, ...], str], str] = {}
    if installed:
        _mark_browser_installed()

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        recorded.append(list(command))
        joined = " ".join(command)
        if _is_curl(command):
            for flag in ("--output", "-o"):
                if flag in command:
                    output = Path(command[command.index(flag) + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(KEY_CONTENT)
                    break
            return FakeProc(0)
        if command[:1] == ["snap"] and command[1:2] == ["list"]:
            if snap_present:
                return FakeProc(0, stdout="firefox 157.0-1\n")
            return FakeProc(1, stderr="error: no matching snaps installed\n")
        if command[:1] == ["snap"] and command[1:2] == ["remove"]:
            # snap remove answers the success code even for an absent snap.
            if snap_present:
                return FakeProc(0, stdout="firefox removed\n")
            return FakeProc(0, stdout='snap "firefox" is not installed\n')
        if "kreadconfig6" in joined:
            return FakeProc(0, stdout=store.get(_kconfig_key(command), ""))
        if "kwriteconfig6" in joined:
            store[_kconfig_key(command)] = command[-1]
            return FakeProc(0)
        if command[:1] == ["apt-get"] and "install" in command:
            if install_creates_binary:
                _mark_browser_installed()
            return FakeProc(0)
        return FakeProc(0, stdout="")

    _patch_run(monkeypatch, fake_run)
    return store


def test_firefox_setup_is_in_desktop_default_set() -> None:
    assert "firefox_setup" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "firefox_setup" not in task_catalog.default_tasks("minimal", REAL_TASKS)
    assert "firefox_setup" not in task_catalog.default_tasks("server", REAL_TASKS)


def test_firefox_setup_has_no_dependency() -> None:
    assert task_catalog.resolve(["firefox_setup"], REAL_TASKS) == ["firefox_setup"]


def test_registers_the_mozilla_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch)
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.success
    assert values.KEYRING_PATH.read_bytes() == KEY_CONTENT
    source = values.APT_SOURCE_PATH.read_text(encoding="utf-8")
    assert "packages.mozilla.org/apt" in source
    assert f"Signed-By: {values.KEYRING_PATH}" in source
    assert "Pin-Priority: 1000" in values.APT_PREFERENCES_PATH.read_text(
        encoding="utf-8"
    )


def test_transitional_package_does_not_count_as_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The Ubuntu archive ships only the transitional package firefox, which
    # installs the snap and provides no browser. dpkg reporting it as installed
    # must not stop the task: the real binary is what counts.
    _write_repository()
    calls: list[list[str]] = []
    _fake_run(monkeypatch, installed=False, calls=calls)
    monkeypatch.setattr("pyntara.utils.package_is_installed", lambda *a, **k: True)
    firefox_setup.task(_ctx(tmp_path))
    installs = [call for call in calls if call[:1] == ["apt-get"] and "install" in call]
    assert installs, "the install was skipped for a machine without the real browser"
    assert "--allow-downgrades" in installs[0]


def test_installed_browser_is_not_reinstalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    calls: list[list[str]] = []
    _fake_run(monkeypatch, installed=True, calls=calls)
    firefox_setup.task(_ctx(tmp_path))
    installs = [call for call in calls if call[:1] == ["apt-get"] and "install" in call]
    assert installs == []


def test_missing_browser_after_install_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch, installed=False, install_creates_binary=False)
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.success
    assert any("has no working browser" in warning for warning in result.warnings)


def test_snap_removal_tolerates_absent_snap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch, snap_present=False)
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.success
    assert not any("snap" in warning for warning in result.warnings)


def test_removed_snap_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch, snap_present=True)
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.message is not None
    assert "removed the snap version of Firefox" in result.message


def test_absent_snap_is_not_reported_as_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # snap remove answers the success code for an absent snap, so the exit code
    # alone must not be read as a removal.
    _write_repository()
    _fake_run(monkeypatch, snap_present=False)
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.message is not None
    assert "removed the snap" not in result.message


def test_browser_is_installed_before_the_snap_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The machine must never lose its browser: the package is installed first and
    # the snap is removed only afterwards.
    _write_repository()
    calls: list[list[str]] = []
    _fake_run(monkeypatch, installed=False, snap_present=True, calls=calls)
    firefox_setup.task(_ctx(tmp_path))
    install_index = next(
        index
        for index, call in enumerate(calls)
        if call[:1] == ["apt-get"] and "install" in call
    )
    snap_index = next(
        index
        for index, call in enumerate(calls)
        if call[:1] == ["snap"] and call[1:2] == ["remove"]
    )
    assert "--terminate" in calls[snap_index]
    assert install_index < snap_index


def test_deploys_the_system_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch)
    firefox_setup.task(_ctx(tmp_path))
    for relative, data in {
        "usr/lib/firefox/distribution/policies.json": POLICY_CONTENT,
        "usr/lib/firefox/defaults/pref/autoconfig.js": AUTOCONFIG_CONTENT,
        "usr/lib/firefox/mozilla.cfg": DEFAULTS_CONTENT,
    }.items():
        assert (values.SYSTEM_ROOT / relative).read_bytes() == data


def test_default_browser_is_written_with_the_kconfig_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    store = _fake_run(monkeypatch)
    firefox_setup.task(_ctx(tmp_path))
    for key in values.DEFAULT_BROWSER_MIME_KEYS:
        assert (
            store[(values.MIMEAPPS_FILE_NAME, values.DEFAULT_BROWSER_GROUP, key)]
            == values.DESKTOP_FILE_NAME
        )


def test_second_run_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch)
    first = firefox_setup.task(_ctx(tmp_path))
    assert first.changed is True
    second = firefox_setup.task(_ctx(tmp_path))
    assert second.changed is False


def test_force_mode_rewrites_the_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch)
    firefox_setup.task(_ctx(tmp_path))
    target = values.SYSTEM_ROOT / "usr/lib/firefox/mozilla.cfg"
    target.write_bytes(b"changed\n")
    forced = firefox_setup.task(_ctx(tmp_path, force=True))
    assert target.read_bytes() == DEFAULTS_CONTENT
    assert forced.changed is True


def test_missing_apt_template_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch)
    monkeypatch.setattr(
        firefox_setup,
        "task_data_dir",
        lambda *args, **kwargs: tmp_path / "absent",
    )
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.success
    assert any("missing apt source template" in warning for warning in result.warnings)


def test_undeclared_value_costs_the_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch)
    monkeypatch.delattr(values, "PACKAGE_NAME", raising=False)
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.success
    assert result.changed is False
    assert any("not declared" in warning for warning in result.warnings)


def test_pins_the_launcher_without_a_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _write_appletsrc()
    store = _fake_run(monkeypatch)
    monkeypatch.setattr(plasma_panel, "session_environment", lambda *a, **k: {})
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.success
    launchers = [
        value
        for (file_name, _groups, key), value in store.items()
        if file_name == common_values.APPLETSRC_FILE_NAME
        and key == common_values.APPLETSRC_LAUNCHERS_KEY
    ]
    assert any(values.PANEL_LAUNCHER_ID in value for value in launchers)
