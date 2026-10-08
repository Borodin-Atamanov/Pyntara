"""Unit tests for the firefox_setup task.

External commands (curl, apt-get, snap, git, pgrep, runuser, xdg-settings,
kreadconfig6, kwriteconfig6) are mocked by monkeypatching run_command in both the
task module and the utils module, because the shared helpers of utils (the
package query and the apt index refresh) call their own run_command; the file
operations run against the temporary tree, because one autouse fixture points
every writable path of the section at the directory of the test
(docs/guides/developer-guide.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import FakeProc, make_context

from pyntara import task_catalog
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


def _patch_run(monkeypatch: pytest.MonkeyPatch, fake_run: object) -> None:
    """Patch run_command in the task module and in the utils module."""

    monkeypatch.setattr(firefox_setup, "run_command", fake_run)
    monkeypatch.setattr("pyntara.utils.run_command", fake_run)


def _is_curl(command: list[str]) -> bool:
    return bool(command) and command[0].endswith("curl")


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: bool = False,
    snap_present: bool = False,
    calls: list[list[str]] | None = None,
) -> None:
    """Replace run_command with a stand-in that answers by argv content."""

    recorded = calls if calls is not None else []

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        recorded.append(list(command))
        joined = " ".join(command)
        if "dpkg-query" in joined:
            if installed:
                return FakeProc(0, stdout="install ok installed\n")
            return FakeProc(1, stdout="deinstall ok config-files\n")
        if _is_curl(command):
            for flag in ("--output", "-o"):
                if flag in command:
                    output = Path(command[command.index(flag) + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(KEY_CONTENT)
                    break
            return FakeProc(0)
        if command[:1] == ["snap"]:
            if snap_present:
                return FakeProc(0, stdout="firefox removed\n")
            return FakeProc(1, stderr="error: no matching snaps installed\n")
        if "xdg-settings" in joined and "get" in command:
            return FakeProc(0, stdout=f"{values.DESKTOP_FILE_NAME}\n")
        return FakeProc(0, stdout="")

    _patch_run(monkeypatch, fake_run)


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


def test_installs_with_allow_downgrades(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    calls: list[list[str]] = []
    _fake_run(monkeypatch, installed=False, calls=calls)
    firefox_setup.task(_ctx(tmp_path))
    installs = [call for call in calls if call[:1] == ["apt-get"] and "install" in call]
    assert installs, "no apt-get install call was made"
    assert "--allow-downgrades" in installs[0]


def test_installed_package_is_not_reinstalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    calls: list[list[str]] = []
    _fake_run(monkeypatch, installed=True, calls=calls)
    firefox_setup.task(_ctx(tmp_path))
    installs = [call for call in calls if call[:1] == ["apt-get"] and "install" in call]
    assert installs == []


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
    assert "removed the snap version of Firefox" in result.message


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


def test_second_run_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch, installed=True)
    first = firefox_setup.task(_ctx(tmp_path))
    assert first.changed is True
    second = firefox_setup.task(_ctx(tmp_path))
    assert second.changed is False


def test_force_mode_rewrites_the_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_repository()
    _fake_run(monkeypatch, installed=True)
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
    appletsrc = Path(common_values.DESKTOP_HOME_DIR) / values.APPLETSRC_RELATIVE_PATH
    appletsrc.parent.mkdir(parents=True, exist_ok=True)
    appletsrc.write_text(APPLETSRC_TEXT, encoding="utf-8")
    written: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        joined = " ".join(command)
        if "dpkg-query" in joined:
            return FakeProc(1, stdout="deinstall ok config-files\n")
        if _is_curl(command):
            return FakeProc(0)
        if "kwriteconfig6" in joined:
            written.append(command[-1])
            return FakeProc(0)
        if "kreadconfig6" in joined:
            return FakeProc(0, stdout="")
        return FakeProc(0)

    _patch_run(monkeypatch, fake_run)
    monkeypatch.setattr(firefox_setup, "session_environment", lambda *a, **k: {})
    result = firefox_setup.task(_ctx(tmp_path))
    assert result.success
    assert any(values.PANEL_LAUNCHER_ID in value for value in written)
