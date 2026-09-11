"""Unit tests for the chrome_setup task.

All external commands (curl, gpg, apt, git, pgrep, runuser) are mocked via
monkeypatch of subprocess.run; the file operations run against the tmp tree
under the configurable paths (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
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
# The local proxy of the repository [three_x_ui_xray_setup] section and the
# flag the override receives while a listener answers on its port.
LOCAL_PROXY_PORT = 10800
CDP_PORT = 19222
PROXY_FLAG = f" --proxy-server=socks5://127.0.0.1:{LOCAL_PROXY_PORT}"
# The launcher id the task pins and the appletsrc groups of the two task
# manager widgets it appears under in the fixture.
PINNED_LAUNCHER = "applications:google-chrome.desktop"
ICON_TASKS_GROUP = (
    "Containments", "2", "Applets", "5", "Configuration", "General",
)
TASKMANAGER_GROUP = (
    "Containments", "7", "Applets", "9", "Configuration", "General",
)
# A Plasma appletsrc with one icons-only and one classic task manager in
# two different panels, mirroring the real pinned launcher layout.
APPLETSRC_TEXT = (
    "[Containments][2]\n"
    "plugin=org.kde.panel\n"
    "\n"
    "[Containments][2][Applets][5]\n"
    "plugin=org.kde.plasma.icontasks\n"
    "\n"
    "[Containments][2][Applets][5][Configuration][General]\n"
    "launchers=applications:org.kde.dolphin.desktop\n"
    "\n"
    "[Containments][7]\n"
    "plugin=org.kde.panel\n"
    "\n"
    "[Containments][7][Applets][9]\n"
    "plugin=org.kde.plasma.taskmanager\n"
    "\n"
    "[Containments][7][Applets][9][Configuration][General]\n"
    "launchers=applications:org.kde.konsole.desktop\n"
)


def _test_config(tmp_path: Path) -> Config:
    """Config whose every writable path lives in the tmp tree."""

    return make_config(
        chrome_home_dir=str(tmp_path / "home"),
        chrome_settings_dir=tmp_path / "repo",
        chrome_system_root=tmp_path / "root",
        chrome_profile_mirror_path=(
            tmp_path / "home" / ".config" / "google-chrome-cdp"
        ),
        chrome_apt_source_path=(
            tmp_path / "etc" / "apt" / "sources.list.d" / "google-chrome.sources"
        ),
        chrome_keyring_path=tmp_path / "usr" / "share" / "keyrings" / "google-chrome.gpg",
        chrome_desktop_source_path=tmp_path / "pkg" / "google-chrome.desktop",
        chrome_desktop_override_path=(
            tmp_path / "usr" / "local" / "share" / "applications" / "google-chrome.desktop"
        ),
        systemd_unit_dir=tmp_path / "systemd",
    )


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    return make_context(
        task_name="chrome_setup",
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


def _write_appletsrc(cfg: ChromeSetupConfig, text: str = APPLETSRC_TEXT) -> None:
    """Create the Plasma appletsrc of the desktop user."""

    path = Path(cfg.home_dir) / ".config" / chrome_setup.APPLETSRC_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _pin_run_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: str = "",
    fail: bool = False,
) -> list[list[str]]:
    """Fake run_command for the pinning helpers; return the calls.

    kreadconfig6 answers the configured current launchers, kwriteconfig6
    records the write, so the helpers run without a real Plasma config.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[:4] == ["runuser", "-u", "i", "--"]:
            inner = command[4:]
            if inner and inner[0] == "kreadconfig6":
                if fail:
                    raise subprocess.CalledProcessError(1, command)
                return _FakeProc(0, current)
            if inner and inner[0] == "kwriteconfig6":
                if fail:
                    raise subprocess.CalledProcessError(1, command)
                return _FakeProc(0, "")
        return _FakeProc(0, "")

    monkeypatch.setattr(chrome_setup, "run_command", fake_run)
    return calls


def _kwrite_group(command: list[str]) -> tuple[str, ...]:
    """The group segments of a recorded kwriteconfig6 command."""

    segments: list[str] = []
    index = 0
    while True:
        try:
            index = command.index("--group", index)
        except ValueError:
            return tuple(segments)
        segments.append(command[index + 1])
        index += 2


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
    local_proxy_listening: bool = True,
    cdp_listening: bool = True,
    mirror_mounted: bool = True,
    mirror_source: str = "",
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    curl writes the armored key bytes to its --output target, gpg copies
    them to the keyring, dpkg-query answers the install state, apt-get and
    git succeed, pgrep answers the Chrome running state, ss answers the
    listener question of the local proxy and of the DevTools port, findmnt
    reports the mirror as a bind mount of the profile directory and runuser
    succeeds. Failure knobs raise CalledProcessError for curl, apt and git;
    the listening and mounting knobs answer the readiness questions.
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
        if name == "ss":
            port = int(command[-1].rsplit(":", 1)[1])
            listening = cdp_listening if port == CDP_PORT else local_proxy_listening
            if not listening:
                return _FakeProc(0, "")
            return _FakeProc(
                0,
                f"LISTEN 0 4096 127.0.0.1:{port} 0.0.0.0:* "
                'users:(("xray",pid=111,fd=7))\n',
            )
        if name == "findmnt":
            if not mirror_mounted:
                return _FakeProc(0, "/ /\n")
            profile_dir = mirror_source or str(
                Path(command[-1]).parent / "google-chrome"
            )
            return _FakeProc(0, f"{command[-1]} {profile_dir}\n")
        if name == "runuser":
            return _FakeProc(0, "")
        return _FakeProc(0, "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_chrome_setup_is_in_desktop_default_set() -> None:
    assert "chrome_setup" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "chrome_setup" not in task_catalog.default_tasks("minimal", REAL_TASKS)
    assert "chrome_setup" not in task_catalog.default_tasks("server", REAL_TASKS)


def test_chrome_setup_depends_on_the_xray_task() -> None:
    resolved = task_catalog.resolve(["chrome_setup"], REAL_TASKS)

    assert resolved.index("three_x_ui_xray_setup") < resolved.index("chrome_setup")
    assert "yggdrasil_service_setup" in resolved


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
    assert config.chrome_setup.profile_mirror_path == Path(
        "/home/i/.config/google-chrome-cdp"
    )
    assert config.chrome_setup.mount_service_unit_name == (
        "mount_chrome_user_dir.service"
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
    content = chrome_setup._desktop_content(
        DESKTOP_SOURCE,
        CDP_PORT,
        "127.0.0.1",
        proxy_server=f"socks5://127.0.0.1:{LOCAL_PROXY_PORT}",
        user_data_dir="/home/i/.config/google-chrome-cdp",
    )
    exec_lines = [
        line for line in content.splitlines() if line.startswith("Exec=")
    ]
    assert len(exec_lines) == 2
    for line in exec_lines:
        assert line.endswith(
            PROXY_FLAG
            + " --user-data-dir=/home/i/.config/google-chrome-cdp"
            + CDP_FLAGS
        )
    assert "Name=Google Chrome" in content


def test_desktop_content_leaves_out_a_flag_that_is_not_ready() -> None:
    content = chrome_setup._desktop_content(
        DESKTOP_SOURCE,
        CDP_PORT,
        "127.0.0.1",
        proxy_server="",
        user_data_dir="",
    )
    exec_lines = [
        line for line in content.splitlines() if line.startswith("Exec=")
    ]
    assert len(exec_lines) == 2
    for line in exec_lines:
        assert line.endswith(CDP_FLAGS)
        assert "--proxy-server" not in line
        assert "--user-data-dir" not in line


def test_local_proxy_server_reads_the_three_x_ui_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _fake_run_factory(monkeypatch)
    client_config = replace(
        ctx.config.three_x_ui_xray_setup, local_proxy_port=10888
    )

    proxy_server, note = chrome_setup._local_proxy_server(
        client_config, timeout=60
    )

    assert proxy_server == "socks5://127.0.0.1:10888"
    assert note is None


def test_local_proxy_server_without_a_listener_returns_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _fake_run_factory(monkeypatch, local_proxy_listening=False)

    proxy_server, note = chrome_setup._local_proxy_server(
        ctx.config.three_x_ui_xray_setup, timeout=60
    )

    assert proxy_server == ""
    assert note is not None
    assert f"no local proxy listens on 127.0.0.1:{LOCAL_PROXY_PORT}" in note


def test_local_proxy_server_without_configured_address_returns_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    calls = _fake_run_factory(monkeypatch)
    client_config = replace(
        ctx.config.three_x_ui_xray_setup, local_proxy_listen_address=""
    )

    proxy_server, note = chrome_setup._local_proxy_server(
        client_config, timeout=60
    )

    assert proxy_server == ""
    assert note is not None
    assert "carries no local proxy address" in note
    assert not calls


def test_full_flow_mounts_the_profile_mirror_and_enables_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    calls = _fake_run_factory(monkeypatch, chrome_installed=False)

    result = chrome_setup.task(ctx)

    assert result.success
    unit_file = ctx.config.engine.systemd_unit_dir / cfg.mount_service_unit_name
    unit_text = unit_file.read_text(encoding="utf-8")
    profile_dir = chrome_setup._profile_dir(cfg.home_dir)
    assert (
        f"ExecStart=/usr/bin/mount --bind {profile_dir} {cfg.profile_mirror_path}"
        in unit_text
    )
    assert f"ExecStartPre=/usr/bin/install -d -o {cfg.username}" in unit_text
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", cfg.mount_service_unit_name] in calls
    override_text = cfg.desktop_override_path.read_text(encoding="utf-8")
    assert PROXY_FLAG in override_text
    assert f" --user-data-dir={cfg.profile_mirror_path}" in override_text


def test_mirror_that_is_not_mounted_keeps_the_user_data_dir_flag_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    _fake_run_factory(monkeypatch, chrome_installed=True, mirror_mounted=False)

    result = chrome_setup.task(ctx)

    assert result.success
    assert any("is not mounted" in warning for warning in result.warnings)
    override_text = cfg.desktop_override_path.read_text(encoding="utf-8")
    assert "--user-data-dir" not in override_text
    assert PROXY_FLAG in override_text
    assert CDP_FLAGS in override_text


def test_cdp_listener_warning_when_chrome_runs_without_the_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    _fake_run_factory(
        monkeypatch,
        chrome_installed=True,
        chrome_running=True,
        cdp_listening=False,
    )

    result = chrome_setup.task(ctx)

    assert result.success
    assert any(
        "the DevTools listener does not answer" in warning
        for warning in result.warnings
    )


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


def test_taskbar_launcher_groups_finds_both_widget_types() -> None:
    groups = chrome_setup._taskbar_launcher_groups(APPLETSRC_TEXT)
    assert len(groups) == 2
    assert ICON_TASKS_GROUP in groups
    assert TASKMANAGER_GROUP in groups


def test_pin_appends_launcher_to_every_taskbar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _test_config(tmp_path).chrome_setup
    _write_appletsrc(cfg)
    calls = _pin_run_fakes(
        monkeypatch, current="applications:org.kde.dolphin.desktop"
    )

    changed, note = chrome_setup._pin_chrome_launcher(cfg, timeout=60)

    assert changed
    assert note is None
    writes = [call for call in calls if "kwriteconfig6" in call]
    assert len(writes) == 2
    write_groups = {_kwrite_group(call) for call in writes}
    assert write_groups == {ICON_TASKS_GROUP, TASKMANAGER_GROUP}
    expected_value = "applications:org.kde.dolphin.desktop," + PINNED_LAUNCHER
    assert all(call[-1] == expected_value for call in writes)


def test_pin_is_idempotent_when_launcher_already_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _test_config(tmp_path).chrome_setup
    _write_appletsrc(cfg)
    calls = _pin_run_fakes(
        monkeypatch,
        current="applications:org.kde.dolphin.desktop," + PINNED_LAUNCHER,
    )

    changed, note = chrome_setup._pin_chrome_launcher(cfg, timeout=60)

    assert not changed
    assert note is None
    assert not any("kwriteconfig6" in call for call in calls)


def test_pin_without_panel_config_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _test_config(tmp_path).chrome_setup
    calls = _pin_run_fakes(monkeypatch)

    changed, note = chrome_setup._pin_chrome_launcher(cfg, timeout=60)

    assert not changed
    assert note is None
    assert not calls


def test_full_flow_pins_launcher_and_restarts_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    cfg = ctx.config.chrome_setup
    _write_repo(cfg)
    _write_desktop_source(cfg)
    _write_appletsrc(cfg)
    calls = _fake_run_factory(monkeypatch, chrome_installed=False)

    result = chrome_setup.task(ctx)

    assert result.success
    assert "pinned the Chrome launcher to the Plasma taskbar" in (
        result.message or ""
    )
    assert any("kwriteconfig6" in call for call in calls)
    restarts = [call for call in calls if call[0] == "systemctl"]
    assert any("plasma-plasmashell.service" in call for call in restarts)


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
    assert not any(
        call[:2] == ["systemctl", "daemon-reload"] for call in calls
    )


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
