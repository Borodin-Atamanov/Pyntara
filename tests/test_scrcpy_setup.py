"""Unit tests for the scrcpy_setup task.

Every external resource (curl, tar, sha256sum, dpkg, apt) is mocked by
patching subprocess.run, so the tests never touch the real system, the real
release or the real packages (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from string import Template
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.config import Config, ScrcpySetupConfig, load_config
from pyntara.context import Context
from pyntara.tasks import scrcpy_setup

# The real catalog from the repository config, so the mode-membership test
# covers the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks

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


def _test_config(tmp_path: Path) -> Config:
    """Config whose home and cache live in the tmp tree."""

    return make_config(
        scrcpy_setup_home_dir=str(tmp_path / "home"),
        scrcpy_setup_download_dir=tmp_path / "cache",
    )


def _config_with(tmp_path: Path, **changes: Any) -> Config:
    """The test config with fields of the scrcpy section replaced."""

    config = _test_config(tmp_path)
    return replace(
        config, scrcpy_setup=replace(config.scrcpy_setup, **changes)
    )


def _ctx(
    tmp_path: Path, *, config: Config | None = None, force: bool = False
) -> Context:
    return make_context(
        install_mode="desktop",
        task_name="scrcpy_setup",
        config=config if config is not None else _test_config(tmp_path),
        force_tasks=frozenset({"scrcpy_setup"}) if force else frozenset(),
    )


def _settings(tmp_path: Path) -> ScrcpySetupConfig:
    return _test_config(tmp_path).scrcpy_setup


def _home(tmp_path: Path) -> Path:
    return Path(_settings(tmp_path).home_dir)


def _install_dir(tmp_path: Path) -> Path:
    return _home(tmp_path) / _settings(tmp_path).install_dir_relative_path


def _version_dir(tmp_path: Path, version: str = VERSION) -> Path:
    return _install_dir(tmp_path) / version


def _command_path(tmp_path: Path) -> Path:
    return _home(tmp_path) / _settings(tmp_path).command_relative_path


def _launcher_path(tmp_path: Path) -> Path:
    return _home(tmp_path) / _settings(tmp_path).launcher_relative_path


def _console_launcher_path(tmp_path: Path) -> Path:
    return _home(tmp_path) / _settings(tmp_path).console_launcher_relative_path


def _trash_dir(tmp_path: Path) -> Path:
    return _home(tmp_path) / _settings(tmp_path).trash_dir_relative_path


def _install_fake_release(tmp_path: Path, version: str = OLD_VERSION) -> Path:
    """Pre-install a complete release tree and point the command at it."""

    settings = _settings(tmp_path)
    tree = _version_dir(tmp_path, version)
    tree.mkdir(parents=True, exist_ok=True)
    for name in (
        settings.binary_file_name,
        settings.server_file_name,
        settings.adb_file_name,
    ):
        (tree / name).write_bytes(b"sentinel\n")
    link = _command_path(tmp_path)
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(tree / settings.binary_file_name), link)
    return tree


def _download_calls(calls: list[list[str]]) -> list[list[str]]:
    """The curl calls that transfer a file, in order."""

    return [call for call in calls if "--output" in call]


def _apt_install_calls(calls: list[list[str]]) -> list[list[str]]:
    """The apt calls that install a package."""

    return [
        call
        for call in calls
        if Path(call[0]).name == "apt-get" and "install" in call
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


def test_real_config_names_the_release_repository() -> None:
    config = load_config(REPO_ROOT / "config")
    assert config.scrcpy_setup.github_repo == "Genymobile/scrcpy"
    assert config.scrcpy_setup.username == "i"
    assert "{asset_arch}" in config.scrcpy_setup.archive_name_template
    assert config.scrcpy_setup.fallback_packages == ("scrcpy",)


def test_install_from_release_points_the_command_at_the_new_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path)
    result = scrcpy_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.warnings == ()
    link = _command_path(tmp_path)
    assert link.is_symlink()
    assert Path(os.readlink(link)) == _version_dir(tmp_path) / "scrcpy"
    for name in ("scrcpy", "scrcpy-server", "adb"):
        assert (_version_dir(tmp_path) / name).is_file()
    assert result.message is not None
    assert "GitHub release" in result.message
    assert any(ASSET_URL in " ".join(call) for call in calls)


def test_release_download_is_verified_against_the_published_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path)
    scrcpy_setup.task(_ctx(tmp_path))
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
    result = scrcpy_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert result.message is not None
    assert "digest" in result.message
    assert not _command_path(tmp_path).is_symlink()
    assert not _version_dir(tmp_path).exists()
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
            "assets": [
                {"name": CHECKSUM_NAME, "browser_download_url": CHECKSUM_URL}
            ],
        }
    )
    apt_binary = tmp_path / "usr-bin-scrcpy"
    apt_binary.write_bytes(b"sentinel\n")
    config = _config_with(tmp_path, apt_binary_path=apt_binary)
    calls, installed = _fake_run_factory(
        monkeypatch, tmp_path, release_json=release_json
    )
    result = scrcpy_setup.task(_ctx(tmp_path, config=config))
    assert "scrcpy" in installed
    assert "scrcpy" in _apt_installed_packages(calls)
    assert result.message is not None
    assert "Ubuntu archive" in result.message
    assert str(apt_binary) in _launcher_path(tmp_path).read_text(encoding="utf-8")


def test_client_that_does_not_answer_falls_back_to_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apt_binary = tmp_path / "usr-bin-scrcpy"
    apt_binary.write_bytes(b"sentinel\n")
    config = _config_with(tmp_path, apt_binary_path=apt_binary)
    _, installed = _fake_run_factory(monkeypatch, tmp_path, client_banner="")
    result = scrcpy_setup.task(_ctx(tmp_path, config=config))
    assert "scrcpy" in installed
    assert not _version_dir(tmp_path).exists()
    assert (_trash_dir(tmp_path) / VERSION).is_dir()
    assert result.warnings


def test_archive_install_failure_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(
        monkeypatch, tmp_path, release_json=None, apt_install_rc=100
    )
    result = scrcpy_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert result.message is not None
    assert "did not install" in result.message
    assert _apt_install_calls(calls)


def test_unavailable_release_keeps_the_installed_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _install_fake_release(tmp_path)
    calls, _ = _fake_run_factory(monkeypatch, tmp_path, release_json=None)
    result = scrcpy_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert Path(os.readlink(_command_path(tmp_path))) == tree / "scrcpy"
    assert "scrcpy" not in _apt_installed_packages(calls)
    assert result.message is not None
    assert "unavailable" in result.message
    assert "keeping the installed scrcpy" in result.message


def test_rerun_with_the_same_version_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path)
    first = scrcpy_setup.task(_ctx(tmp_path))
    assert first.changed is True
    downloads = len(_download_calls(calls))
    second = scrcpy_setup.task(_ctx(tmp_path))
    assert second.changed is False
    assert second.message is not None
    assert "already installed scrcpy" in second.message
    assert len(_download_calls(calls)) == downloads


def test_force_reinstalls_the_same_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path)
    scrcpy_setup.task(_ctx(tmp_path))
    downloads = len(_download_calls(calls))
    forced = scrcpy_setup.task(_ctx(tmp_path, force=True))
    assert forced.changed is True
    assert len(_download_calls(calls)) > downloads


def test_superseded_version_moves_into_the_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_release(tmp_path)
    _fake_run_factory(monkeypatch, tmp_path)
    result = scrcpy_setup.task(_ctx(tmp_path))
    assert result.changed is True
    assert not _version_dir(tmp_path, OLD_VERSION).exists()
    assert (_trash_dir(tmp_path) / OLD_VERSION).is_dir()
    assert Path(os.readlink(_command_path(tmp_path))) == (
        _version_dir(tmp_path) / "scrcpy"
    )


def test_menu_entries_start_the_client_and_keep_the_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run_factory(monkeypatch, tmp_path)
    scrcpy_setup.task(_ctx(tmp_path))
    client = str(_version_dir(tmp_path) / "scrcpy")
    icon = str(_version_dir(tmp_path) / "scrcpy.png")
    launcher = _launcher_path(tmp_path).read_text(encoding="utf-8")
    console = _console_launcher_path(tmp_path).read_text(encoding="utf-8")
    assert f"Exec={client}\n" in launcher
    assert f"Icon={icon}\n" in launcher
    assert "Terminal=false" in launcher
    assert "Terminal=true" in console
    assert f"Exec={client} --pause-on-exit=if-error\n" in console


def test_menu_entries_are_rendered_from_the_real_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run_factory(monkeypatch, tmp_path)
    scrcpy_setup.task(_ctx(tmp_path))
    settings = _settings(tmp_path)
    for template_name, target in (
        (settings.launcher_template_file_name, _launcher_path(tmp_path)),
        (
            settings.console_launcher_template_file_name,
            _console_launcher_path(tmp_path),
        ),
    ):
        template = Template(
            (
                REPO_ROOT / "task_data" / "scrcpy_setup" / template_name
            ).read_text(encoding="utf-8")
        )
        expected = template.substitute(
            binary=str(_version_dir(tmp_path) / settings.binary_file_name),
            icon=str(_version_dir(tmp_path) / settings.icon_file_name),
        )
        assert target.read_text(encoding="utf-8") == expected


def test_android_usb_rules_failure_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _ = _fake_run_factory(monkeypatch, tmp_path, apt_install_rc=100)
    result = scrcpy_setup.task(_ctx(tmp_path))
    assert result.changed is True
    assert result.message is not None
    assert "android-udev-rules" in result.message
    assert "unreachable" in result.message
    # One initial attempt plus the configured retries, all for the rules
    # package: the release path never touches apt.
    assert set(_apt_installed_packages(calls)) == {UDEV_PACKAGE}
