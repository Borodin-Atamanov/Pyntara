"""Unit tests for the telegram_setup task.

All external resources (curl, tar, runuser) are mocked via monkeypatch of
subprocess.run; the tests never touch the real system or the real Telegram
download (docs/guides/developer-guide.md). The cache directory and the home of
the desktop user are values, so one autouse fixture points them at the temporary
directory of the test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import telegram_setup
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import tasks as tasks_values
from pyntara.values import telegram_setup as values

# The release url the fake redirect resolves to and its archive name. The
# name mirrors the current Telegram scheme and is arbitrary to the task: no
# name or format is assumed, the cache file takes the basename as is.
FINAL_URL = "https://td.telegram.org/linux-x64/td-setup-linux-x64-1.2.3.tar.xz"
ARCHIVE_NAME = "td-setup-linux-x64-1.2.3.tar.xz"
OLD_ARCHIVE_NAME = "td-setup-linux-x64-0.9.0.tar.xz"

# Sentinel bytes the fake tar puts into the extracted Telegram files and
# the fake curl writes to every --output target.
TELEGRAM_BYTES = b"\x7fELF-sentinel-telegram-binary\n"
UPDATER_BYTES = b"sentinel-updater-binary\n"
ARCHIVE_BYTES = b"sentinel-archive\n"
ICON_BYTES = b"sentinel-icon-png\n"

# The real catalog from the values package; the mode-membership and
# config tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = tasks_values.CATALOG


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own cache directory and user home.

    Both are values: the cache directory of this section and the home of the
    desktop user, which comes from the shared module. The fixture points them
    at the temporary directory of the test and the shipped values come back
    afterwards, so no test writes into a real cache or a real home.
    """

    monkeypatch.setattr(values, "DOWNLOAD_DIR", tmp_path / "cache")
    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(tmp_path / "home"))


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    """Context safe for unit tests; the real paths are never touched."""

    return make_context(
        install_mode="desktop",
        task_name="telegram_setup",
        force_tasks=frozenset({"telegram_setup"}) if force else frozenset(),
    )


def _home() -> Path:
    return Path(common_values.DESKTOP_HOME_DIR)


def _template_path() -> Path:
    """The launcher template as it lives in the clone.

    The path is built here from the documented layout instead of calling
    the task, so a drifted directory or file name shows up as a failure.
    """

    return (
        REPO_ROOT / "task_data" / "telegram_setup" / values.LAUNCHER_TEMPLATE_FILE_NAME
    )


def _deployed_paths() -> tuple[Path, Path, Path, Path]:
    """The binary, the updater, the launcher entry and the icon."""

    home = _home()
    install_dir = home / values.INSTALL_DIR_RELATIVE_PATH
    return (
        install_dir / values.BINARY_FILE_NAME,
        install_dir / values.UPDATER_FILE_NAME,
        home / values.LAUNCHER_RELATIVE_PATH,
        home / values.ICON_RELATIVE_PATH,
    )


def _fake_run_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    resolved_url: str = FINAL_URL,
    head_rc: int = 0,
    probe_rc: int = 0,
    download_rc: int = 0,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    The reachability probe is the only curl call of the task that carries
    no retry flag, and the resolve is the engine curl call whose template
    names the effective url: the probe answers probe_rc, the resolve answers
    resolved_url (or fails with head_rc), and every other curl call writes
    the sentinel bytes to its --output target (the icon or the archive
    download). tar creates the extracted binaries under its --directory, in
    the archive directory and under the names the values module names, so a
    test that renames them exercises the same layout the task reads. A
    nonzero download_rc makes every download fail, which stands for a failed
    archive or icon download.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "curl":
            if "--retry" not in command:
                return _FakeProc(probe_rc, "")
            if any("%{url_effective}" in part for part in command):
                return _FakeProc(head_rc, stdout=resolved_url if head_rc == 0 else "")
            if download_rc != 0 and kwargs.get("check", False):
                raise subprocess.CalledProcessError(download_rc, command)
            out_index = command.index("--output") + 1
            Path(command[out_index]).write_bytes(ICON_BYTES)
            return _FakeProc(download_rc, stdout="Downloaded sentinel bytes\n")
        if command[0] == "tar":
            dir_index = command.index("--directory") + 1
            archive_dir = Path(command[dir_index]) / values.ARCHIVE_DIRECTORY_NAME
            archive_dir.mkdir(parents=True, exist_ok=True)
            (archive_dir / values.BINARY_FILE_NAME).write_bytes(TELEGRAM_BYTES)
            (archive_dir / values.UPDATER_FILE_NAME).write_bytes(UPDATER_BYTES)
            return _FakeProc(0, "")
        return _FakeProc(0, "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_telegram_setup_is_in_desktop_default_set() -> None:
    assert "telegram_setup" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "telegram_setup" not in task_catalog.default_tasks("minimal", REAL_TASKS)
    assert "telegram_setup" not in task_catalog.default_tasks("server", REAL_TASKS)


def test_the_shipped_values_name_the_desktop_user_and_the_official_link() -> None:
    # The shipped values are the ones a real run reads, so they are checked
    # here without a config document.
    assert common_values.DESKTOP_USERNAME == "i"
    assert values.LATEST_URL == "https://telegram.org/dl/desktop/linux"
    assert values.ICON_URL.endswith("icon512.png")


def test_cache_name_takes_the_redirect_basename_as_is() -> None:
    assert telegram_setup._cache_name(FINAL_URL) == ARCHIVE_NAME
    # The old tsetup naming is accepted the same way: no name scheme is
    # assumed.
    old_url = "https://td.telegram.org/tlinux/tsetup.1.2.3.tar.xz"
    assert telegram_setup._cache_name(old_url) == "tsetup.1.2.3.tar.xz"


def test_desktop_content_points_at_the_installed_binary(tmp_path: Path) -> None:
    binary, _updater, _launcher, icon = _deployed_paths()
    content = telegram_setup._desktop_content(_template_path())
    assert f"Exec={binary}" in content
    assert f"Icon={icon}" in content
    assert content.startswith("[Desktop Entry]")
    assert "Name=Telegram Desktop" in content


def test_desktop_content_follows_the_configured_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The launcher body lives in the template and its two paths in the
    # values: another install directory, another binary name and another
    # icon path must change the rendered entry.
    monkeypatch.setattr(values, "INSTALL_DIR_RELATIVE_PATH", "opt/telegram")
    monkeypatch.setattr(values, "BINARY_FILE_NAME", "telegram-desktop")
    monkeypatch.setattr(values, "ICON_RELATIVE_PATH", ".icons/custom.png")
    binary, _updater, _launcher, icon = _deployed_paths()
    content = telegram_setup._desktop_content(_template_path())
    assert f"Exec={binary}" in content
    assert f"Icon={icon}" in content
    assert "$binary" not in content and "$icon" not in content


def test_install_downloads_and_installs_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run_factory(monkeypatch, tmp_path)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert "installed Telegram Desktop" in (result.message or "")
    binary, updater, launcher, icon = _deployed_paths()
    assert binary.read_bytes() == TELEGRAM_BYTES
    assert updater.read_bytes() == UPDATER_BYTES
    assert launcher.is_file()
    assert f"Exec={binary}" in launcher.read_text(encoding="utf-8")
    assert icon.read_bytes() == ICON_BYTES
    archive = tmp_path / "cache" / ARCHIVE_NAME
    assert archive.is_file()
    assert not (
        tmp_path / "cache" / (ARCHIVE_NAME + engine_values.PARTIAL_DOWNLOAD_FILE_SUFFIX)
    ).exists()
    assert any(call[0] == "tar" for call in calls)


def test_already_installed_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary, _updater, launcher, icon = _deployed_paths()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(TELEGRAM_BYTES)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        telegram_setup._desktop_content(_template_path()),
        encoding="utf-8",
    )
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(ICON_BYTES)
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)

    calls = _fake_run_factory(monkeypatch, tmp_path)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert "already installed the latest release" in (result.message or "")
    assert not any(call[0] == "tar" for call in calls)
    archive_downloads = [
        call for call in calls if call[0] == "curl" and "--head" not in call
    ]
    assert archive_downloads == []


def test_newer_release_replaces_and_removes_the_stale_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary, _updater, _launcher, _icon = _deployed_paths()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"old binary\n")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / OLD_ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)

    calls = _fake_run_factory(monkeypatch, tmp_path)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert binary.read_bytes() == TELEGRAM_BYTES
    assert (cache / ARCHIVE_NAME).is_file()
    assert not (cache / OLD_ARCHIVE_NAME).exists()
    assert any(call[0] == "tar" for call in calls)


def test_force_reinstalls_when_the_latest_archive_is_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary, _updater, _launcher, _icon = _deployed_paths()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(TELEGRAM_BYTES)
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)

    calls = _fake_run_factory(monkeypatch, tmp_path)
    result = telegram_setup.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "tar" for call in calls)


def test_resolve_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The redirect cannot be resolved: the reason is a warning and the
    # launcher entry is still written, because it does not depend on the
    # release URL.
    _fake_run_factory(monkeypatch, tmp_path, head_rc=22)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert any("cannot resolve" in warning for warning in result.warnings)
    assert _deployed_paths()[2].is_file()


def test_a_silent_host_is_reported_without_spending_the_retry_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A host that never answers is a blocked destination: the probe reports
    # it once, the resolve never runs, and its retry budget is not spent on
    # silence. The launcher entry is still written, because it does not
    # depend on the release url.
    calls = _fake_run_factory(monkeypatch, tmp_path, probe_rc=28)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert any("did not answer within" in warning for warning in result.warnings)
    assert not any(any("%{url_effective}" in part for part in call) for call in calls)
    assert not any(call[0] == "tar" for call in calls)
    assert _deployed_paths()[2].is_file()


def test_the_probe_budget_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The probe budget is a value: it reaches the probe call and the
    # warning, so a slow machine is answered in the values module and not in
    # code.
    monkeypatch.setattr(values, "REACHABILITY_PROBE_TIMEOUT_SECONDS", 45)
    calls = _fake_run_factory(monkeypatch, tmp_path, probe_rc=28)
    result = telegram_setup.task(_ctx(tmp_path))
    probes = [call for call in calls if call[0] == "curl" and "--retry" not in call]
    assert len(probes) == 1
    assert probes[0][probes[0].index("--connect-timeout") + 1] == "45"
    assert any("within 45 s" in warning for warning in result.warnings)


def test_a_slow_host_that_answers_is_still_resolved_and_downloaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The probe separates a reachable host from a silent one only: an answer,
    # however slow, leaves the resolve with the retry settings of the engine
    # table, and the archive of the release is downloaded.
    calls = _fake_run_factory(monkeypatch, tmp_path)
    result = telegram_setup.task(_ctx(tmp_path))
    resolves = [
        call for call in calls if any("%{url_effective}" in part for part in call)
    ]
    assert len(resolves) == 1
    assert "--retry" in resolves[0]
    assert result.changed is True
    assert (tmp_path / "cache" / ARCHIVE_NAME).is_file()


def test_download_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The archive cannot be downloaded: the reason is a warning, nothing is
    # installed and the launcher entry is still written.
    _fake_run_factory(monkeypatch, tmp_path, download_rc=22)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert any("cannot download" in warning for warning in result.warnings)
    assert not _deployed_paths()[0].exists()


def test_configured_names_decide_what_is_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another layout in the values is what the task writes: the relative
    # paths, the two binary names, the archive directory and the suffix of
    # the partial download are read, not composed in code.
    monkeypatch.setattr(values, "INSTALL_DIR_RELATIVE_PATH", "opt/telegram")
    monkeypatch.setattr(
        values,
        "LAUNCHER_RELATIVE_PATH",
        ".local/share/applications/custom.desktop",
    )
    monkeypatch.setattr(values, "ICON_RELATIVE_PATH", ".local/share/icons/custom.png")
    monkeypatch.setattr(values, "BINARY_FILE_NAME", "telegram-desktop")
    monkeypatch.setattr(values, "UPDATER_FILE_NAME", "upgrade-helper")
    monkeypatch.setattr(values, "ARCHIVE_DIRECTORY_NAME", "telegram-archive")
    _fake_run_factory(monkeypatch, tmp_path)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    binary, updater, launcher, icon = _deployed_paths()
    assert binary.read_bytes() == TELEGRAM_BYTES
    assert updater.read_bytes() == UPDATER_BYTES
    assert f"Exec={binary}" in launcher.read_text(encoding="utf-8")
    assert icon.read_bytes() == ICON_BYTES
    assert (tmp_path / "cache" / ARCHIVE_NAME).is_file()
    suffix = engine_values.PARTIAL_DOWNLOAD_FILE_SUFFIX
    assert not (tmp_path / "cache" / (ARCHIVE_NAME + suffix)).exists()


def test_icon_failure_is_a_warning_when_install_is_current(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary, _updater, launcher, _icon = _deployed_paths()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(TELEGRAM_BYTES)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        telegram_setup._desktop_content(_template_path()),
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)

    # download_rc makes the icon curl fail while the resolve curl keeps the
    # head_rc of zero.
    calls = _fake_run_factory(monkeypatch, tmp_path, download_rc=22)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert any("icon" in warning for warning in result.warnings)
    # The install is current, so nothing is extracted and no archive is
    # downloaded; only the icon curl is attempted and fails.
    assert not any(call[0] == "tar" for call in calls)
