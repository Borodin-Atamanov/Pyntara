"""Unit tests for the chrome_setup task.

All external commands (curl, gpg, apt, git, pgrep, runuser) are mocked via
monkeypatch of subprocess.run; the file operations run against the tmp tree
under the configurable paths (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.config import ChromeSetupConfig, Config, load_config
from pyntara.context import Context
from pyntara.tasks import chrome_setup

# The real catalog from the repository config; the mode-membership and
# config tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks

# Fixture content of the browser settings repository: the profile
# preferences and the system/ tree files.
PREFERENCES_CONTENT = {
    "browser": {"theme": "repo-dark"},
    "extensions": {"settings": {"enabled": True}},
}
SYSTEM_FILES = {
    "etc/opt/chrome/policies/managed/chrome.json": b'{"policy": true}\n',
    "opt/google/chrome/extensions/abcdefghijklmnop.json": (
        b'{"external_update_url": "https://example.invalid/update"}\n'
    ),
}
# The packaged Chrome desktop entry used as the override source.
DESKTOP_SOURCE = (
    "[Desktop Entry]\n"
    "Name=Google Chrome\n"
    "Exec=/usr/bin/google-chrome-stable %U\n"
    "Icon=google-chrome\n"
    "Actions=new-window;\n"
    "\n"
    "[Desktop Action new-window]\n"
    "Name=New Window\n"
    "Exec=/usr/bin/google-chrome-stable\n"
)
CDP_FLAGS = " --remote-debugging-port=19222 --remote-debugging-address=127.0.0.1"


def _test_config(tmp_path: Path) -> Config:
    """Config whose every writable path lives in the tmp tree."""

    return make_config(
        chrome_home_dir=str(tmp_path / "home"),
        chrome_settings_dir=tmp_path / "repo",
        chrome_system_root=tmp_path / "root",
        chrome_apt_source_path=(
            tmp_path / "etc" / "apt" / "sources.list.d" / "google-chrome.sources"
        ),
        chrome_keyring_path=tmp_path / "usr" / "share" / "keyrings" / "google-chrome.gpg",
        chrome_desktop_source_path=tmp_path / "pkg" / "google-chrome.desktop",
        chrome_desktop_override_path=(
            tmp_path / "usr" / "local" / "share" / "applications" / "google-chrome.desktop"
        ),
    )


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    return make_context(
        install_mode="desktop",
        config=_test_config(tmp_path),
        force_tasks=frozenset({"chrome_setup"}) if force else frozenset(),
    )


def _write_repo(cfg: ChromeSetupConfig) -> None:
    """Create the settings repository as if already cloned."""

    settings_dir = cfg.settings_dir
    (settings_dir / ".git").mkdir(parents=True, exist_ok=True)
    prefs_dir = settings_dir / "Default"
    prefs_dir.mkdir(parents=True, exist_ok=True)
    (prefs_dir / "Preferences").write_text(
        json.dumps(PREFERENCES_CONTENT, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for rel, data in SYSTEM_FILES.items():
        path = settings_dir / "system" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _write_desktop_source(cfg: ChromeSetupConfig) -> None:
    """Create the packaged Chrome desktop entry."""

    source = cfg.desktop_source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(DESKTOP_SOURCE, encoding="utf-8")


def _profile_path(cfg: ChromeSetupConfig) -> Path:
    return Path(cfg.home_dir) / ".config" / "google-chrome" / "Default" / "Preferences"


def _fake_run_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chrome_installed: bool = True,
    chrome_running: bool = False,
    curl_fail: bool = False,
    apt_fail: bool = False,
    git_fail: bool = False,
    git_head: str = "a" * 40,
    git_fetch: str = "a" * 40,
    clone_creates_dir: bool = False,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    curl writes the armored key bytes to its --output target, gpg copies
    them to the keyring, dpkg-query answers the install state, apt-get and
    git succeed, pgrep answers the Chrome running state and runuser
    succeeds. Failure knobs raise CalledProcessError for curl, apt and git.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        name = command[0]
        if name == "curl":
            if curl_fail and kwargs.get("check", True):
                raise subprocess.CalledProcessError(7, command)
            out_index = command.index("--output") + 1
            Path(command[out_index]).write_bytes(b"armored-key")
            return _FakeProc(0, "")
        if name == "gpg":
            out_index = command.index("--output") + 1
            Path(command[out_index]).write_bytes(Path(command[-1]).read_bytes())
            return _FakeProc(0, "")
        if name == "dpkg-query":
            if chrome_installed:
                return _FakeProc(0, "install ok installed")
            return _FakeProc(1, "")
        if name == "apt-get":
            if apt_fail and command[1] == "install":
                raise subprocess.CalledProcessError(100, command)
            return _FakeProc(0, "")
        if name == "git":
            if git_fail:
                raise subprocess.CalledProcessError(128, command)
            if "clone" in command:
                if clone_creates_dir:
                    Path(command[-1]).mkdir(parents=True, exist_ok=True)
                return _FakeProc(0, "")
            if "rev-parse" in command:
                ref = command[-1]
                return _FakeProc(0, git_fetch if ref == "FETCH_HEAD" else git_head)
            return _FakeProc(0, "")
        if name == "pgrep":
            return _FakeProc(0 if chrome_running else 1, "")
        if name == "runuser":
            return _FakeProc(0, "")
        return _FakeProc(0, "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_chrome_setup_is_in_desktop_default_set() -> None:
    assert "chrome_setup" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "chrome_setup" not in task_catalog.default_tasks("minimal", REAL_TASKS)
    assert "chrome_setup" not in task_catalog.default_tasks("server", REAL_TASKS)


def test_real_config_names_google_repo_cdp_and_system_root() -> None:
    config = load_config(REPO_ROOT / "config")
    assert config.chrome_setup.username == "i"
    assert config.chrome_setup.settings_repo_url.endswith(
        "chromium-default-settings.git"
    )
    assert config.chrome_setup.settings_repo_ref == "main"
    assert config.chrome_setup.system_root == Path("/")
    assert config.chrome_setup.cdp_port == 19222
    assert config.chrome_setup.cdp_address == "127.0.0.1"
    assert config.chrome_setup.desktop_override_path == Path(
        "/usr/local/share/applications/google-chrome.desktop"
    )


def test_merge_preferences_overlay_wins_and_keeps_unrelated() -> None:
    current = {"a": 1, "nested": {"x": 1, "y": 2}, "keep": "v", "lst": [1, 2]}
    overlay = {"a": 2, "nested": {"y": 3}, "new": "n", "lst": [9]}
    merged = chrome_setup._merge_preferences(current, overlay)
    assert merged == {
        "a": 2,
        "nested": {"x": 1, "y": 3},
        "keep": "v",
        "new": "n",
        "lst": [9],
    }


def test_source_text_mentions_google_repo_and_keyring() -> None:
    text = chrome_setup._source_text(Path("/etc/apt/keyrings/google-chrome.gpg"))
    assert "URIs: https://dl.google.com/linux/chrome-stable/deb/" in text
    assert "Signed-By: /etc/apt/keyrings/google-chrome.gpg" in text


def test_desktop_content_appends_flags_to_each_exec() -> None:
    content = chrome_setup._desktop_content(DESKTOP_SOURCE, 19222, "127.0.0.1")
    exec_lines = [
        line for line in content.splitlines() if line.startswith("Exec=")
    ]
    assert len(exec_lines) == 2
    for line in exec_lines:
        assert line.endswith(CDP_FLAGS)
    assert "Name=Google Chrome" in content


def test_full_flow_applies_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    calls = _fake_run_factory(monkeypatch, chrome_installed=False)

    result = chrome_setup.task(ctx)

    assert result.success
    assert result.changed
    assert not result.warnings
    assert cfg.apt_source_path.is_file()
    assert cfg.keyring_path.is_file()
    assert cfg.keyring_path.stat().st_size > 0
    assert (
        chrome_setup._source_text(cfg.keyring_path)
        in cfg.apt_source_path.read_text(encoding="utf-8")
    )
    assert ["apt-get", "install", "-y", "google-chrome-stable"] in calls
    policy = cfg.system_root / "etc" / "opt" / "chrome" / "policies" / "managed" / "chrome.json"
    assert policy.read_bytes() == SYSTEM_FILES[
        "etc/opt/chrome/policies/managed/chrome.json"
    ]
    extension = cfg.system_root / "opt" / "google" / "chrome" / "extensions" / "abcdefghijklmnop.json"
    assert extension.read_bytes() == SYSTEM_FILES[
        "opt/google/chrome/extensions/abcdefghijklmnop.json"
    ]
    assert json.loads(_profile_path(cfg).read_text(encoding="utf-8")) == (
        PREFERENCES_CONTENT
    )
    override_text = cfg.desktop_override_path.read_text(encoding="utf-8")
    assert CDP_FLAGS in override_text
    menu_calls = [call for call in calls if "kbuildsycoca6" in call]
    assert menu_calls
    assert "XDG_MENU_PREFIX=plasma-" in menu_calls[0]


def test_menu_refresh_carries_the_plasma_menu_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _test_config(tmp_path).chrome_setup
    calls = _fake_run_factory(monkeypatch)

    assert chrome_setup._refresh_menu_database(cfg, timeout=60) is None

    menu_calls = [call for call in calls if "kbuildsycoca6" in call]
    assert len(menu_calls) == 1
    command = menu_calls[0]
    assert command[:4] == ["runuser", "-u", cfg.username, "--"]
    assert command[4] == "env"
    assert f"HOME={cfg.home_dir}" in command
    assert "XDG_MENU_PREFIX=plasma-" in command
    assert command[-2:] == ["kbuildsycoca6", "--noincremental"]


def test_second_run_changes_nothing_when_target_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    _fake_run_factory(monkeypatch, chrome_installed=True)
    assert chrome_setup.task(ctx).success

    calls = _fake_run_factory(monkeypatch, chrome_installed=True)
    result = chrome_setup.task(ctx)

    assert result.success
    assert not result.changed
    assert "already set up" in (result.message or "")
    assert not any(call[0] == "curl" for call in calls)
    assert not any(call[0] == "apt-get" for call in calls)


def test_merge_restores_repo_value_keeping_unrelated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    _fake_run_factory(monkeypatch, chrome_installed=True)
    assert chrome_setup.task(ctx).success
    prefs = _profile_path(cfg)
    data = json.loads(prefs.read_text(encoding="utf-8"))
    data["browser"]["theme"] = "user-pick"
    data["extra"] = "keep-me"
    prefs.write_text(json.dumps(data, indent=2), encoding="utf-8")

    _fake_run_factory(monkeypatch, chrome_installed=True)
    result = chrome_setup.task(ctx)

    assert result.success
    assert result.changed
    after = json.loads(prefs.read_text(encoding="utf-8"))
    assert after["browser"]["theme"] == "repo-dark"
    assert after["extra"] == "keep-me"
    assert after["extensions"] == PREFERENCES_CONTENT["extensions"]


def test_profile_left_untouched_when_chrome_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    _fake_run_factory(monkeypatch, chrome_installed=True, chrome_running=True)

    result = chrome_setup.task(ctx)

    assert result.success
    assert not _profile_path(cfg).exists()
    assert any("Chrome is running" in warning for warning in result.warnings)


def test_force_rewrites_files_and_reinstalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path, force=True)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    calls = _fake_run_factory(monkeypatch, chrome_installed=True)

    result = chrome_setup.task(ctx)

    assert result.success
    assert result.changed
    assert ["apt-get", "install", "-y", "google-chrome-stable"] in calls


def test_repository_failure_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    _fake_run_factory(monkeypatch, chrome_installed=True, curl_fail=True)

    result = chrome_setup.task(ctx)

    assert not result.success
    assert "cannot register the Google Chrome apt repository" in (result.error or "")


def test_install_failure_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    _fake_run_factory(monkeypatch, chrome_installed=False, apt_fail=True)

    result = chrome_setup.task(ctx)

    assert not result.success
    assert "cannot install google-chrome-stable" in (result.error or "")


def test_desktop_override_warns_when_packaged_entry_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _fake_run_factory(monkeypatch, chrome_installed=True)

    result = chrome_setup.task(ctx)

    assert result.success
    assert not cfg.desktop_override_path.exists()
    assert any(
        "packaged desktop entry is missing" in warning for warning in result.warnings
    )


def test_sync_clones_missing_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    assert not cfg.settings_dir.exists()
    _fake_run_factory(monkeypatch, clone_creates_dir=True)

    changed, error = chrome_setup._sync_settings_repo(cfg, timeout=10)

    assert changed
    assert error is None
    assert cfg.settings_dir.is_dir()


def test_sync_updates_when_remote_advanced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    calls = _fake_run_factory(monkeypatch, git_head="a" * 40, git_fetch="b" * 40)

    changed, error = chrome_setup._sync_settings_repo(cfg, timeout=10)

    assert changed
    assert error is None
    assert any("reset" in call for call in calls)


def test_sync_leaves_current_repository_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    calls = _fake_run_factory(monkeypatch)

    changed, error = chrome_setup._sync_settings_repo(cfg, timeout=10)

    assert not changed
    assert error is None
    assert not any("reset" in call for call in calls)
