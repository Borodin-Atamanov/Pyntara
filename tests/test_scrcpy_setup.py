"""Unit tests for the scrcpy_setup task.

Every external resource (curl, tar, sha256sum, dpkg, apt) is mocked by
patching subprocess.run, so the tests never touch the real system, the real
release or the real packages (docs/guides/developer-guide.md). The home of the
desktop user and the download cache are values, so one autouse fixture points
them at the temporary directory of the test.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from string import Template

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import scrcpy_setup
from pyntara.values import common as common_values
from pyntara.values import scrcpy_setup as values
from pyntara.values import tasks as tasks_values

# The real catalog from the values package, so the mode-membership test
# covers the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = tasks_values.CATALOG

TAG = "v4.1"
VERSION = "4.1"
OLD_VERSION = "3.3.4"
ARCHITECTURE = "amd64"
ASSET_NAME = "scrcpy-linux-x86_64-v4.1.tar.gz"
ASSET_URL = f"https://example.invalid/releases/download/{TAG}/{ASSET_NAME}"
CHECKSUM_NAME = "SHA256SUMS.txt"
CHECKSUM_URL = f"https://example.invalid/releases/download/{TAG}/{CHECKSUM_NAME}"
DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64
TREE_DIR_NAME = "scrcpy-linux-x86_64-v4.1"
UDEV_PACKAGE = "android-udev-rules"
CLIENT_BANNER = f"scrcpy {VERSION} <https://github.com/Genymobile/scrcpy>\n"
RELEASE_JSON = json.dumps(
    {
        "tag_name": TAG,
        "assets": [
            {"name": ASSET_NAME, "browser_download_url": ASSET_URL},
            {"name": CHECKSUM_NAME, "browser_download_url": CHECKSUM_URL},
        ],
    }
)


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own home directory and cache.

    Both are values: the home of the desktop user comes from the shared module
    and the download cache from this section. The fixture points them at the
    temporary directory of the test and the shipped values come back
    afterwards, so no test writes into a real home or a real cache.
    """

    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.setattr(values, "DOWNLOAD_DIR", tmp_path / "cache")


def _ctx(*, force: bool = False) -> Context:
    """Context safe for unit tests; the real paths are never touched."""

    return make_context(
        install_mode="desktop",
        task_name="scrcpy_setup",
        force_tasks=frozenset({"scrcpy_setup"}) if force else frozenset(),
    )


def _home() -> Path:
    return Path(common_values.DESKTOP_HOME_DIR)


def _install_dir() -> Path:
    return _home() / values.INSTALL_DIR_RELATIVE_PATH


def _version_dir(version: str = VERSION) -> Path:
    return _install_dir() / version


def _command_path() -> Path:
    return _home() / values.COMMAND_RELATIVE_PATH


def _launcher_path() -> Path:
    return _home() / values.LAUNCHER_RELATIVE_PATH


def _console_launcher_path() -> Path:
    return _home() / values.CONSOLE_LAUNCHER_RELATIVE_PATH


def _trash_dir() -> Path:
    return _home() / values.TRASH_DIR_RELATIVE_PATH


def _install_fake_release(version: str = OLD_VERSION) -> Path:
    """Pre-install a complete release tree and point the command at it."""

    tree = _version_dir(version)
    tree.mkdir(parents=True, exist_ok=True)
    for name in (
        values.BINARY_FILE_NAME,
        values.SERVER_FILE_NAME,
        values.ADB_FILE_NAME,
    ):
        (tree / name).write_bytes(b"sentinel\n")
    link = _command_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(tree / values.BINARY_FILE_NAME), link)
    return tree


def _download_calls(calls: list[list[str]]) -> list[list[str]]:
    """The curl calls that transfer a file, in order."""

    return [call for call in calls if "--output" in call]


def _apt_install_calls(calls: list[list[str]]) -> list[list[str]]:
    """The apt calls that install a package."""

    return [
        call for call in calls if Path(call[0]).name == "apt-get" and "install" in call
    ]


def _apt_installed_packages(calls: list[list[str]]) -> list[str]:
    """The packages the apt install calls asked for, in order."""

    return [call[-1] for call in _apt_install_calls(calls)]


def _fake_run_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    release_json: str | None = RELEASE_JSON,
    published_digest: str = DIGEST,
    printed_digest: str = DIGEST,
    client_banner: str = CLIENT_BANNER,
    installed_packages: tuple[str, ...] = (),
    apt_install_rc: int = 0,
) -> tuple[list[list[str]], set[str]]:
    """Install a subprocess.run fake; return the calls and the installed set.

    The fake answers every external tool the task runs: dpkg reports the
    architecture, the release query answers the release JSON (or fails when
    it is None, which stands for an unreachable GitHub), a download writes
    the archive bytes or the published checksum file to its --output target,
    sha256sum prints the digest a test chose, tar unpacks a tree with the
    three release files into its --directory, the client answers its version
    query, and apt installs the requested package into the installed set. A
    test can therefore make any single step fail and check what the task does
    with it.
    """

    calls: list[list[str]] = []
    installed = set(installed_packages)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        executable = Path(command[0]).name
        if executable == "dpkg":
            return _FakeProc(0, f"{ARCHITECTURE}\n")
        if executable == "dpkg-query":
            if command[-1] in installed:
                return _FakeProc(0, "install ok installed\n")
            return _FakeProc(1, "unknown ok not-installed\n")
        if executable == "apt-get":
            if "install" in command:
                if apt_install_rc != 0:
                    raise subprocess.CalledProcessError(apt_install_rc, command)
                installed.add(command[-1])
                return _FakeProc(0, "")
            return _FakeProc(0, "")
        if executable == "curl":
            if "api.github.com" in " ".join(command):
                # The release query runs with check=False, so an unreachable
                # GitHub is a nonzero exit and never a raised exception.
                if release_json is None:
                    return _FakeProc(22, "")
                return _FakeProc(0, release_json)
            target = Path(command[command.index("--output") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            # The download target is the partial file, whose name starts with
            # the published name; the checksum file holds text and everything
            # else stands for the archive bytes.
            if target.name.startswith(CHECKSUM_NAME):
                target.write_text(
                    f"{published_digest}  {ASSET_NAME}\n", encoding="utf-8"
                )
            else:
                target.write_bytes(b"archive sentinel bytes\n")
            return _FakeProc(0, "")
        if executable == "sha256sum":
            return _FakeProc(0, f"{printed_digest}  {Path(command[-1]).name}\n")
        if executable == "tar":
            directory = Path(command[command.index("--directory") + 1])
            tree = directory / TREE_DIR_NAME
            tree.mkdir(parents=True, exist_ok=True)
            for name in ("scrcpy", "scrcpy-server", "adb"):
                (tree / name).write_bytes(b"sentinel\n")
            return _FakeProc(0, "")
        if "--version" in command:
            if not client_banner:
                return _FakeProc(1, "")
            return _FakeProc(0, client_banner)
        return _FakeProc(0, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls, installed


def test_scrcpy_setup_is_in_desktop_default_set() -> None:
    assert "scrcpy_setup" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "scrcpy_setup" not in task_catalog.default_tasks("minimal", REAL_TASKS)
    assert "scrcpy_setup" not in task_catalog.default_tasks("server", REAL_TASKS)


def test_the_shipped_values_name_the_release_repository() -> None:
    # The shipped values are the ones a real run reads, so they are checked
    # here without its own values module.
    assert values.GITHUB_REPO == "Genymobile/scrcpy"
    assert common_values.DESKTOP_USERNAME == "i"
    assert "{asset_arch}" in values.ARCHIVE_NAME_TEMPLATE
    assert values.FALLBACK_PACKAGES == ("scrcpy",)


def test_install_from_release_points_the_command_at_the_new_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path)
    result = scrcpy_setup.task(_ctx())
    assert result.success is True
    assert result.changed is True
    assert result.warnings == ()
    link = _command_path()
    assert link.is_symlink()
    assert Path(os.readlink(link)) == _version_dir() / "scrcpy"
    for name in ("scrcpy", "scrcpy-server", "adb"):
        assert (_version_dir() / name).is_file()
    assert result.message is not None
    assert "GitHub release" in result.message
    assert any(ASSET_URL in " ".join(call) for call in calls)


def test_release_download_is_verified_against_the_published_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path)
    scrcpy_setup.task(_ctx())
    downloads = [call[-1] for call in _download_calls(calls)]
    assert any(name.endswith(ASSET_NAME) for name in downloads)
    assert any(name.endswith(CHECKSUM_NAME) for name in downloads)
    assert any(Path(call[0]).name == "sha256sum" for call in calls)


def test_checksum_mismatch_keeps_the_machine_and_never_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(
        monkeypatch,
        tmp_path,
        printed_digest=OTHER_DIGEST,
        installed_packages=(UDEV_PACKAGE,),
    )
    result = scrcpy_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert result.message is not None
    assert "digest" in result.message
    assert not _command_path().is_symlink()
    assert not _version_dir().exists()
    # The archive and the checksum file are fetched twice: one retry, then
    # the release is abandoned. The Ubuntu archive is never taken, because
    # an integrity alarm must not replace the installation with an older one.
    assert len(_download_calls(calls)) == 4
    assert _apt_install_calls(calls) == []


def test_missing_asset_falls_back_to_the_ubuntu_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_json = json.dumps(
        {
            "tag_name": TAG,
            "assets": [{"name": CHECKSUM_NAME, "browser_download_url": CHECKSUM_URL}],
        }
    )
    apt_binary = tmp_path / "usr-bin-scrcpy"
    apt_binary.write_bytes(b"sentinel\n")
    monkeypatch.setattr(values, "APT_BINARY_PATH", apt_binary)
    calls, installed = _fake_run_factory(
        monkeypatch, tmp_path, release_json=release_json
    )
    result = scrcpy_setup.task(_ctx())
    assert "scrcpy" in installed
    assert "scrcpy" in _apt_installed_packages(calls)
    assert result.message is not None
    assert "Ubuntu archive" in result.message
    assert str(apt_binary) in _launcher_path().read_text(encoding="utf-8")


def test_client_that_does_not_answer_falls_back_to_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apt_binary = tmp_path / "usr-bin-scrcpy"
    apt_binary.write_bytes(b"sentinel\n")
    monkeypatch.setattr(values, "APT_BINARY_PATH", apt_binary)
    _, installed = _fake_run_factory(monkeypatch, tmp_path, client_banner="")
    result = scrcpy_setup.task(_ctx())
    assert "scrcpy" in installed
    assert not _version_dir().exists()
    assert (_trash_dir() / VERSION).is_dir()
    assert result.warnings


def test_archive_install_failure_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(
        monkeypatch, tmp_path, release_json=None, apt_install_rc=100
    )
    result = scrcpy_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert result.message is not None
    assert "did not install" in result.message
    assert _apt_install_calls(calls)


def test_unavailable_release_keeps_the_installed_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _install_fake_release()
    calls, _ = _fake_run_factory(monkeypatch, tmp_path, release_json=None)
    result = scrcpy_setup.task(_ctx())
    assert result.success is True
    assert Path(os.readlink(_command_path())) == tree / "scrcpy"
    assert "scrcpy" not in _apt_installed_packages(calls)
    assert result.message is not None
    assert "unavailable" in result.message
    assert "keeping the installed scrcpy" in result.message


def test_rerun_with_the_same_version_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path)
    first = scrcpy_setup.task(_ctx())
    assert first.changed is True
    downloads = len(_download_calls(calls))
    second = scrcpy_setup.task(_ctx())
    assert second.changed is False
    assert second.message is not None
    assert "already installed scrcpy" in second.message
    assert len(_download_calls(calls)) == downloads


def test_force_reinstalls_the_same_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path)
    scrcpy_setup.task(_ctx())
    downloads = len(_download_calls(calls))
    forced = scrcpy_setup.task(_ctx(force=True))
    assert forced.changed is True
    assert len(_download_calls(calls)) > downloads


def test_superseded_version_moves_into_the_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_release()
    _fake_run_factory(monkeypatch, tmp_path)
    result = scrcpy_setup.task(_ctx())
    assert result.changed is True
    assert not _version_dir(OLD_VERSION).exists()
    assert (_trash_dir() / OLD_VERSION).is_dir()
    assert Path(os.readlink(_command_path())) == (_version_dir() / "scrcpy")


def test_menu_entries_start_the_client_and_keep_the_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run_factory(monkeypatch, tmp_path)
    scrcpy_setup.task(_ctx())
    client = str(_version_dir() / "scrcpy")
    icon = str(_version_dir() / "scrcpy.png")
    launcher = _launcher_path().read_text(encoding="utf-8")
    console = _console_launcher_path().read_text(encoding="utf-8")
    assert f"Exec={client}\n" in launcher
    assert f"Icon={icon}\n" in launcher
    assert "Terminal=false" in launcher
    assert "Terminal=true" in console
    assert f"Exec={client} --pause-on-exit=if-error\n" in console


def test_menu_entries_are_rendered_from_the_real_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run_factory(monkeypatch, tmp_path)
    scrcpy_setup.task(_ctx())
    for template_name, target in (
        (values.LAUNCHER_TEMPLATE_FILE_NAME, _launcher_path()),
        (
            values.CONSOLE_LAUNCHER_TEMPLATE_FILE_NAME,
            _console_launcher_path(),
        ),
    ):
        template = Template(
            (REPO_ROOT / "task_data" / "scrcpy_setup" / template_name).read_text(
                encoding="utf-8"
            )
        )
        expected = template.substitute(
            binary=str(_version_dir() / values.BINARY_FILE_NAME),
            icon=str(_version_dir() / values.ICON_FILE_NAME),
        )
        assert target.read_text(encoding="utf-8") == expected


def test_android_usb_rules_failure_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path, apt_install_rc=100)
    result = scrcpy_setup.task(_ctx())
    assert result.changed is True
    assert result.message is not None
    assert "android-udev-rules" in result.message
    assert "unreachable" in result.message
    # One initial attempt plus the configured retries, all for the rules
    # package: the release path never touches apt.
    assert set(_apt_installed_packages(calls)) == {UDEV_PACKAGE}
