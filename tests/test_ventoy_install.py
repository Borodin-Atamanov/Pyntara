"""Unit tests for the ventoy_install task.

Every external resource (curl, tar, bash, ldconfig, dpkg) is mocked by patching
subprocess.run, so the tests never touch the real system, the real release or
the real install directory. The install directory, the current link, the
download cache and the menu entry are values, so one autouse fixture points
them at the temporary directory of the test.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from string import Template

import pytest
from support import REPO_ROOT, make_context
from support import FakeProc as _FakeProc

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import ventoy_install
from pyntara.utils import task_data_dir
from pyntara.values import tasks as tasks_values
from pyntara.values import ventoy_install as values

TAG = "v1.1.18"
VERSION = "1.1.18"
OLD_VERSION = "1.1.17"
ARCHITECTURE = "amd64"
ASSET_ARCH = "x86_64"
ASSET_NAME = f"ventoy-{VERSION}-linux.tar.gz"
ASSET_URL = f"https://example.invalid/releases/download/{TAG}/{ASSET_NAME}"
TREE_DIR_NAME = f"ventoy-{VERSION}"
GUI_BINARY_NAME = f"VentoyGUI.{ASSET_ARCH}"
ACCEPTANCE_BANNER = f"Ventoy: {VERSION}  {ASSET_ARCH}\nUsage: Ventoy2Disk.sh\n"
GUI_LIBRARY_CACHE = (
    "libgtk-3.so.0 (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libgtk-3.so.0\n"
)
RELEASE_JSON = json.dumps(
    {
        "tag_name": TAG,
        "assets": [
            {"name": ASSET_NAME, "browser_download_url": ASSET_URL},
            {"name": "sha256.txt", "browser_download_url": ASSET_URL + ".sha"},
        ],
    }
)


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own install tree and cache.

    The install directory, the current link, the download cache and the menu
    entry are values: the fixture points them at the temporary directory of
    the test and the shipped values come back afterwards, so no test writes
    into /opt or into the applications directory of the machine.
    """

    install_dir = tmp_path / "opt" / "ventoy"
    monkeypatch.setattr(values, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(values, "CURRENT_LINK_PATH", install_dir / "current")
    monkeypatch.setattr(values, "DOWNLOAD_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        values, "DESKTOP_ENTRY_PATH", tmp_path / "applications" / "ventoy.desktop"
    )


def _ctx(*, force: bool = False, delete_packages_after_install: bool = True) -> Context:
    """Context safe for unit tests; the real paths are never touched."""

    return make_context(
        install_mode="desktop",
        task_name="ventoy_install",
        force_tasks=frozenset({"ventoy_install"}) if force else frozenset(),
        delete_packages_after_install=delete_packages_after_install,
    )


def _install_dir() -> Path:
    return Path(values.INSTALL_DIR)


def _current_link() -> Path:
    return Path(values.CURRENT_LINK_PATH)


def _desktop_entry() -> Path:
    return Path(values.DESKTOP_ENTRY_PATH)


def _version_dir(version: str = VERSION) -> Path:
    return _install_dir() / version


def _superseded_dir(version: str = VERSION) -> Path:
    return _install_dir() / (version + values.SUPERSEDED_SUFFIX)


def _write_tree(tree: Path, version: str) -> None:
    """Write the files a complete release tree carries."""

    for relative_path in values.TREE_MARKER_FILES:
        path = tree / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative_path == values.VERSION_FILE_RELATIVE_PATH:
            path.write_text(version + "\n", encoding="utf-8")
        else:
            path.write_bytes(b"sentinel\n")
    (tree / GUI_BINARY_NAME).write_bytes(b"sentinel\n")
    icon = tree / values.ICON_RELATIVE_PATH
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(b"png\n")


def _desktop_entry_text() -> str:
    """The menu entry the task renders for the shipped template."""

    template_path = (
        task_data_dir(REPO_ROOT, "ventoy_install") / values.DESKTOP_TEMPLATE_FILE_NAME
    )
    return Template(template_path.read_text(encoding="utf-8")).substitute(
        gui_path=str(_current_link() / GUI_BINARY_NAME),
        icon_path=str(_current_link() / values.ICON_RELATIVE_PATH),
    )


def _install_release(version: str = VERSION) -> Path:
    """Pre-install a complete release tree, its link and its menu entry."""

    tree = _version_dir(version)
    _write_tree(tree, version)
    link = _current_link()
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(str(tree), link)
    entry = _desktop_entry()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(_desktop_entry_text(), encoding="utf-8")
    return tree


def _download_calls(calls: list[list[str]]) -> list[list[str]]:
    """The curl calls that transfer a file, in order."""

    return [call for call in calls if "--output" in call]


def _fake_run_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    release_json: str | None = RELEASE_JSON,
    download_rc: int = 0,
    acceptance_rc: int = 0,
    acceptance_text: str = ACCEPTANCE_BANNER,
    linker_cache: str = GUI_LIBRARY_CACHE,
    tree_version: str = VERSION,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded calls.

    The fake answers every external tool the task runs: dpkg reports the
    architecture, the release query answers the release JSON (or fails when it
    is None, which stands for an unreachable GitHub), a download writes the
    archive bytes, tar unpacks one tree with the release files, bash answers
    the acceptance query of the tree and ldconfig lists the graphical
    libraries. A test can therefore make any single step fail and check what
    the task does with it.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        executable = Path(command[0]).name
        if executable == "dpkg":
            return _FakeProc(0, f"{ARCHITECTURE}\n")
        if executable == "curl":
            if "api.github.com" in " ".join(command):
                # The release query runs with check=False, so an unreachable
                # GitHub is a nonzero exit and never a raised exception.
                if release_json is None:
                    return _FakeProc(22, "")
                return _FakeProc(0, release_json)
            target = Path(command[command.index("--output") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            if download_rc != 0:
                raise subprocess.CalledProcessError(download_rc, command)
            target.write_bytes(b"archive sentinel bytes\n")
            return _FakeProc(0, "")
        if executable == "tar":
            directory = Path(command[command.index("--directory") + 1])
            _write_tree(directory / TREE_DIR_NAME, tree_version)
            return _FakeProc(0, "")
        if executable == "bash":
            if "-h" not in command:
                # Without the help flag the front end prints its usage and
                # exits one, which the acceptance query must not accept.
                return _FakeProc(1, acceptance_text)
            if acceptance_rc != 0:
                return _FakeProc(acceptance_rc, "")
            return _FakeProc(0, acceptance_text)
        if executable == "ldconfig":
            return _FakeProc(0, linker_cache)
        return _FakeProc(0, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_ventoy_install_is_in_desktop_and_fast_desktop_default_sets() -> None:
    catalog = tasks_values.CATALOG
    assert "ventoy_install" in task_catalog.default_tasks("desktop", catalog)
    assert "ventoy_install" in task_catalog.default_tasks("fast_desktop", catalog)
    assert "ventoy_install" not in task_catalog.default_tasks("minimal", catalog)
    assert "ventoy_install" not in task_catalog.default_tasks("server", catalog)


def test_the_shipped_values_name_the_release_repository() -> None:
    # The shipped values are the ones a real run reads, so they are checked
    # here without its own values module. The paths are not checked here:
    # the fixture of this file points them at the temporary tree.
    assert values.GITHUB_REPO == "ventoy/Ventoy"
    assert values.RELEASE_TAG_PREFIX == "v"
    assert values.ARCHIVE_NAME_TEMPLATE.format(version=VERSION) == (
        "ventoy-1.1.18-linux.tar.gz"
    )
    assert values.TREE_MARKER_FILES == (
        "boot/boot.img",
        "ventoy/version",
        "Ventoy2Disk.sh",
    )


def test_install_from_release_points_the_current_link_at_the_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_run_factory(monkeypatch)
    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is True
    assert result.warnings == ()
    link = _current_link()
    assert link.is_symlink()
    assert Path(os.readlink(link)) == _version_dir(VERSION)
    for relative_path in values.TREE_MARKER_FILES:
        assert (_version_dir(VERSION) / relative_path).is_file()
    assert (_version_dir(VERSION) / GUI_BINARY_NAME).is_file()
    assert result.message is not None
    assert f"installed Ventoy {VERSION}" in result.message
    assert any(ASSET_URL in " ".join(call) for call in calls)
    # The unpacked tree is asked its own version before the link is switched.
    assert any(Path(call[0]).name == "bash" for call in calls)


def test_the_menu_entry_starts_the_guarded_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The entry starts the launcher as the desktop user and never wraps it in
    # pkexec: pkexec alone strips DISPLAY and XAUTHORITY, and the launcher
    # raises its own privileges in the session it was started from.
    _fake_run_factory(monkeypatch)
    ventoy_install.task(_ctx())

    text = _desktop_entry().read_text(encoding="utf-8")
    lines = text.splitlines()
    assert f"Exec={_current_link() / GUI_BINARY_NAME}" in lines
    assert f"Icon={_current_link() / values.ICON_RELATIVE_PATH}" in lines
    assert "Terminal=false" in lines
    assert all("pkexec" not in line for line in lines)


def test_the_second_run_changes_nothing_and_downloads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_release()
    calls = _fake_run_factory(monkeypatch)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert result.warnings == ()
    assert result.message is not None
    assert f"already installed Ventoy {VERSION}" in result.message
    assert _download_calls(calls) == []
    assert not any(Path(call[0]).name == "tar" for call in calls)


def test_a_newer_release_switches_the_link_and_keeps_the_previous_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_release(OLD_VERSION)
    _fake_run_factory(monkeypatch)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is True
    assert Path(os.readlink(_current_link())) == _version_dir(VERSION)
    # The tree of the previous version stays on the machine and is never
    # deleted, so a release the user did not want is recoverable.
    assert (_version_dir(OLD_VERSION) / GUI_BINARY_NAME).is_file()


def test_the_cached_archive_is_removed_when_the_run_deletes_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run_factory(monkeypatch)
    result = ventoy_install.task(_ctx())

    assert result.changed is True
    assert list(Path(values.DOWNLOAD_DIR).iterdir()) == []


def test_the_cached_archive_is_kept_when_the_run_keeps_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run_factory(monkeypatch)
    result = ventoy_install.task(_ctx(delete_packages_after_install=False))

    assert result.changed is True
    assert (Path(values.DOWNLOAD_DIR) / ASSET_NAME).is_file()


def test_an_unreachable_release_keeps_the_installed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_release()
    calls = _fake_run_factory(monkeypatch, release_json=None)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert Path(os.readlink(_current_link())) == _version_dir(VERSION)
    assert any("GitHub release is unavailable" in item for item in result.warnings)
    assert result.message is not None
    assert f"keeping the installed Ventoy {VERSION}" in result.message
    assert not any(Path(call[0]).name == "tar" for call in calls)


def test_an_unreachable_release_without_an_install_reports_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run_factory(monkeypatch, release_json=None)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert result.message is not None
    assert result.message.startswith("Ventoy was not installed")
    assert any("GitHub release is unavailable" in item for item in result.warnings)
    assert not _current_link().exists()


def test_a_failed_download_reports_a_warning_and_keeps_the_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_release(OLD_VERSION)
    before = os.readlink(_current_link())
    _fake_run_factory(monkeypatch, download_rc=22)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert os.readlink(_current_link()) == before
    assert any(ASSET_URL in item for item in result.warnings)
    assert not _version_dir(VERSION).exists()


def test_an_unusable_unpack_is_never_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run_factory(monkeypatch, acceptance_rc=1)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.message is not None
    assert result.message.startswith("Ventoy was not installed")
    assert any("unpacked tree" in item for item in result.warnings)
    assert not _current_link().exists()
    assert not _version_dir(VERSION).exists()
    # The temporary unpack directory never survives a failed step.
    assert not any(
        entry.name.startswith(values.EXTRACT_DIR_PREFIX)
        for entry in _install_dir().iterdir()
    )


def test_an_unpack_that_names_another_version_is_never_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The archive of the newest release must carry that release: a tree whose
    # own version file says something else is not the tree the run asked for.
    _fake_run_factory(monkeypatch, tree_version=OLD_VERSION)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert any("complete Ventoy tree" in item for item in result.warnings)
    assert not _current_link().exists()


def test_force_rebuilds_the_same_version_and_keeps_the_previous_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_release()
    _fake_run_factory(monkeypatch)

    result = ventoy_install.task(_ctx(force=True))

    assert result.success is True
    assert result.changed is True
    assert Path(os.readlink(_current_link())) == _version_dir(VERSION)
    assert _superseded_dir().is_dir()
    assert (_superseded_dir() / GUI_BINARY_NAME).is_file()


def test_a_broken_tree_is_replaced_by_a_complete_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _install_release()
    (tree / "boot" / "boot.img").unlink()
    _fake_run_factory(monkeypatch)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is True
    assert (_version_dir(VERSION) / "boot" / "boot.img").is_file()
    assert Path(os.readlink(_current_link())) == _version_dir(VERSION)


def test_a_stale_menu_entry_is_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_release()
    _desktop_entry().write_text("[Desktop Entry]\nExec=/somewhere/else\n", "utf-8")
    _fake_run_factory(monkeypatch)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is True
    assert _desktop_entry().read_text(encoding="utf-8") == _desktop_entry_text()


def test_a_machine_without_a_toolkit_reports_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run_factory(monkeypatch, linker_cache="libc.so.6 => /lib/libc.so.6\n")

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is True
    assert any("the Ventoy window will not start" in item for item in result.warnings)


def test_the_task_reports_a_missing_value_instead_of_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(values, "GITHUB_REPO")
    calls = _fake_run_factory(monkeypatch)

    result = ventoy_install.task(_ctx())

    assert result.success is True
    assert result.changed is False
    assert result.message == (
        "the ventoy_install values are not declared, nothing was changed"
    )
    assert any("GITHUB_REPO" in item for item in result.warnings)
    assert calls == []
