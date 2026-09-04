"""Unit tests for the telegram_setup task.

All external resources (curl, tar, runuser) are mocked via monkeypatch of
subprocess.run; the tests never touch the real system or the real Telegram
download (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.config import Config, load_config
from pyntara.context import Context
from pyntara.tasks import telegram_setup

# The release url the fake redirect resolves to and its archive name.
FINAL_URL = "https://td.telegram.org/tlinux/tsetup.1.2.3.tar.xz"
ARCHIVE_NAME = "tsetup.1.2.3.tar.xz"
OLD_ARCHIVE_NAME = "tsetup.0.9.0.tar.xz"

# Sentinel bytes the fake tar puts into the extracted Telegram files and
# the fake curl writes to every --output target.
TELEGRAM_BYTES = b"\x7fELF-sentinel-telegram-binary\n"
UPDATER_BYTES = b"sentinel-updater-binary\n"
ARCHIVE_BYTES = b"sentinel-archive\n"
ICON_BYTES = b"sentinel-icon-png\n"

# The real catalog from the repository config; the mode-membership and
# config tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks


def _test_config(tmp_path: Path, **overrides: object) -> Config:
    """Config whose home and cache live in the tmp tree."""

    return make_config(
        telegram_home_dir=str(tmp_path / "home"),
        telegram_download_dir=tmp_path / "cache",
        **overrides,
    )


def _ctx(tmp_path: Path, *, force: bool = False, **overrides: object) -> Context:
    return make_context(
        install_mode="desktop",
        config=_test_config(tmp_path, **overrides),
        force_tasks=frozenset({"telegram_setup"}) if force else frozenset(),
    )


def _home(cfg: Config) -> Path:
    return Path(cfg.telegram_setup.home_dir)


def _fake_run_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    resolved_url: str = FINAL_URL,
    head_rc: int = 0,
    download_rc: int = 0,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    A curl with --head answers the redirect resolution with resolved_url, a
    curl with --output writes the sentinel bytes to its target (the icon or
    the archive download) and tar creates the extracted Telegram files under
    its --directory. A nonzero download_rc makes every non-head curl fail,
    which stands for a failed archive or icon download.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if "--head" in command:
            return _FakeProc(head_rc, stdout=resolved_url if head_rc == 0 else "")
        if command[0] == "curl":
            if download_rc != 0 and kwargs.get("check", False):
                raise subprocess.CalledProcessError(download_rc, command)
            out_index = command.index("--output") + 1
            Path(command[out_index]).write_bytes(ICON_BYTES)
            return _FakeProc(download_rc, stdout="Downloaded sentinel bytes\n")
        if command[0] == "tar":
            dir_index = command.index("--directory") + 1
            telegram_dir = Path(command[dir_index]) / "Telegram"
            telegram_dir.mkdir(parents=True, exist_ok=True)
            (telegram_dir / telegram_setup.BINARY_NAME).write_bytes(TELEGRAM_BYTES)
            (telegram_dir / telegram_setup.UPDATER_NAME).write_bytes(UPDATER_BYTES)
            return _FakeProc(0, "")
        return _FakeProc(0, "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_telegram_setup_is_in_desktop_default_set() -> None:
    assert "telegram_setup" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "telegram_setup" not in task_catalog.default_tasks("minimal", REAL_TASKS)
    assert "telegram_setup" not in task_catalog.default_tasks("server", REAL_TASKS)


def test_real_config_names_the_desktop_user_and_the_official_link() -> None:
    config = load_config(REPO_ROOT / "config")
    assert config.telegram_setup.username == "i"
    assert config.telegram_setup.latest_url == "https://telegram.org/dl/desktop/linux"
    assert config.telegram_setup.icon_url.endswith("icon512.png")


def test_archive_name_accepts_tsetup_archives_only() -> None:
    assert telegram_setup._archive_name(FINAL_URL) == ARCHIVE_NAME
    with pytest.raises(RuntimeError, match="unexpected latest download URL"):
        telegram_setup._archive_name("https://example.invalid/other.tar.xz")


def test_desktop_content_points_at_the_installed_binary(tmp_path: Path) -> None:
    config = _test_config(tmp_path)
    home = _home(config)
    content = telegram_setup._desktop_content(config.telegram_setup)
    assert f"Exec={home / '.local/share/Telegram/Telegram'}" in content
    assert f"Icon={home / '.local/share/icons/telegram-desktop.png'}" in content
    assert content.startswith("[Desktop Entry]")
    assert "Name=Telegram Desktop" in content


def test_install_downloads_and_installs_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run_factory(monkeypatch)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert "installed Telegram Desktop" in (result.message or "")
    config = _test_config(tmp_path)
    home = _home(config)
    binary = home / ".local/share/Telegram/Telegram"
    updater = home / ".local/share/Telegram/Updater"
    assert binary.read_bytes() == TELEGRAM_BYTES
    assert updater.read_bytes() == UPDATER_BYTES
    launcher = home / ".local/share/applications/telegramdesktop.desktop"
    assert launcher.is_file()
    assert f"Exec={binary}" in launcher.read_text(encoding="utf-8")
    icon = home / ".local/share/icons/telegram-desktop.png"
    assert icon.read_bytes() == ICON_BYTES
    archive = tmp_path / "cache" / ARCHIVE_NAME
    assert archive.is_file()
    assert not (tmp_path / "cache" / (ARCHIVE_NAME + ".download")).exists()
    assert any(call[0] == "tar" for call in calls)


def test_already_installed_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _test_config(tmp_path)
    home = _home(config)
    binary = home / ".local/share/Telegram/Telegram"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(TELEGRAM_BYTES)
    launcher = home / ".local/share/applications/telegramdesktop.desktop"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        telegram_setup._desktop_content(config.telegram_setup), encoding="utf-8"
    )
    icon = home / ".local/share/icons/telegram-desktop.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(ICON_BYTES)
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)

    calls = _fake_run_factory(monkeypatch)
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
    config = _test_config(tmp_path)
    home = _home(config)
    binary = home / ".local/share/Telegram/Telegram"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"old binary\n")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / OLD_ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)

    calls = _fake_run_factory(monkeypatch)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert binary.read_bytes() == TELEGRAM_BYTES
    assert (cache / ARCHIVE_NAME).is_file()
    assert not (cache / OLD_ARCHIVE_NAME).exists()


def test_force_reinstalls_when_the_latest_archive_is_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _test_config(tmp_path)
    home = _home(config)
    binary = home / ".local/share/Telegram/Telegram"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(TELEGRAM_BYTES)
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)

    calls = _fake_run_factory(monkeypatch)
    result = telegram_setup.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "tar" for call in calls)


def test_resolve_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_run_factory(monkeypatch, head_rc=22)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is False
    assert "cannot resolve" in (result.error or "")


def test_download_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_run_factory(monkeypatch, download_rc=22)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is False
    assert "cannot download" in (result.error or "")


def test_icon_failure_is_a_warning_when_install_is_current(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _test_config(tmp_path)
    home = _home(config)
    binary = home / ".local/share/Telegram/Telegram"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(TELEGRAM_BYTES)
    launcher = home / ".local/share/applications/telegramdesktop.desktop"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        telegram_setup._desktop_content(config.telegram_setup), encoding="utf-8"
    )
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ARCHIVE_NAME).write_bytes(ARCHIVE_BYTES)

    # download_rc makes the icon curl fail while the resolve curl keeps the
    # head_rc of zero.
    calls = _fake_run_factory(monkeypatch, download_rc=22)
    result = telegram_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert any("icon" in warning for warning in result.warnings)
