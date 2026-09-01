"""Unit tests for the ffmpeg_setup task.

All external resources (dpkg-query, apt-get, pkg-config, gcc) are mocked
via monkeypatch; the tests never touch the real system
(docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.config import MODES, Config, load_config
from pyntara.context import Context
from pyntara.tasks import ffmpeg_setup

# Package set used by the tests; mirrors the real config but stays small.
TEST_PACKAGES = ("ffmpeg",)

# The real catalog from the repository config; the mode-membership and
# dependency tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks

# The bytes the fake gcc writes to its output file; a deployed engine that
# carries these bytes counts as already built.
WAYRECORD_BINARY = b"\x7fELF-sentinel-wayrecord-binary\n"
WAYRECORD_C = "int main(void) { return 0; }\n"
ZKDE_CLIENT_C = "/* generated wayland protocol stubs */\n"


def _wayrecord_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path]:
    """Point the C sources and the deploy targets at tmp; return (bin, desktop).

    REPO_ROOT is monkeypatched to a fixture clone that carries the wayrecord
    C sources under task_data/ffmpeg_setup/, and the target binary plus the
    desktop entry live in the tmp tree so the real /usr and /usr/local are
    never touched.
    """

    repo = tmp_path / "repo"
    template_dir = repo / "task_data" / "ffmpeg_setup"
    template_dir.mkdir(parents=True)
    (template_dir / "wayrecord.c").write_text(WAYRECORD_C, encoding="utf-8")
    (template_dir / "zkde-screencast-client.c").write_text(
        ZKDE_CLIENT_C, encoding="utf-8"
    )
    monkeypatch.setattr(ffmpeg_setup, "REPO_ROOT", repo)
    return (
        tmp_path / "bin" / "pyntara-wayrecord",
        tmp_path / "applications" / "pyntara-wayrecord.desktop",
    )


def _test_config(
    wayrecord_bin_path: Path, wayrecord_desktop_path: Path
) -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    return make_config(
        ffmpeg_setup_packages=TEST_PACKAGES,
        ffmpeg_setup_wayrecord_bin_path=wayrecord_bin_path,
        ffmpeg_setup_wayrecord_desktop_path=wayrecord_desktop_path,
    )


def _ctx(
    wayrecord_bin_path: Path,
    wayrecord_desktop_path: Path,
    *,
    skip_apt_update: bool = False,
) -> Context:
    return make_context(
        config=_test_config(wayrecord_bin_path, wayrecord_desktop_path),
        skip_apt_update=skip_apt_update,
    )


def _command_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: set[str],
    install_rc: int = 0,
    build_rc: int = 0,
    pkgconfig_rc: int = 0,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query answers from the installed set, apt-get install answers with
    install_rc, pkg-config returns the build flags and gcc writes the
    sentinel WAYRECORD_BINARY to its -o target. Every command is recorded.
    A nonzero return with check=True raises exactly like the real
    subprocess.run.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        rc = 0
        stdout = ""
        if command[0] == "dpkg-query":
            if command[-1] in installed:
                return _FakeProc(0, "install ok installed\n")
            rc = 1
        elif command[0] == "apt-get" and command[1] == "install":
            rc = install_rc
        elif command[0] == "pkg-config":
            stdout = (
                "-I/usr/include/pipewire-0.3 -I/usr/include/spa-0.2 "
                "-lwayland-client -lpipewire-0.3"
            )
            rc = pkgconfig_rc
        elif command[0] == "gcc":
            out_index = command.index("-o") + 1
            Path(command[out_index]).write_bytes(WAYRECORD_BINARY)
            rc = build_rc
        if rc != 0 and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, command, stdout)
        return _FakeProc(rc, stdout)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_ffmpeg_setup_is_in_every_mode_default_set() -> None:
    for mode in MODES:
        assert "ffmpeg_setup" in task_catalog.default_tasks(mode, REAL_TASKS)


def test_ffmpeg_setup_depends_on_add_extra_repos() -> None:
    # ffmpeg lives in universe, so add_extra_repos is a hard dependency,
    # the same as imagemagick_setup and cli_tools.
    task_def = task_catalog.by_name("ffmpeg_setup", REAL_TASKS)
    assert task_def is not None
    assert task_def.depends == ("add_extra_repos",)


def test_real_config_names_the_meta_package() -> None:
    # The real config must name the real package ffmpeg, not a virtual
    # name, so dpkg-query sees it as installed.
    config = load_config(REPO_ROOT / "config")
    assert "ffmpeg" in config.ffmpeg_setup.packages
    assert config.ffmpeg_setup.wayrecord_bin_path.name == "pyntara-wayrecord"
    assert config.ffmpeg_setup.wayrecord_desktop_path.name == (
        "pyntara-wayrecord.desktop"
    )
    # The build toolchain is part of the package set.
    for build_dep in ("gcc", "libwayland-dev", "libpipewire-0.3-dev", "pkgconf"):
        assert build_dep in config.ffmpeg_setup.packages


def test_all_installed_skips_apt_and_rebuild(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    wayrecord_bin_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_bin_path.write_bytes(WAYRECORD_BINARY)
    wayrecord_bin_path.chmod(0o755)
    wayrecord_desktop_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_desktop_path.write_text(
        ffmpeg_setup._desktop_content(wayrecord_bin_path), encoding="utf-8"
    )
    calls = _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)
    # The engine is always rebuilt to check staleness, but a matching
    # target is left untouched.
    assert any(call[0] == "gcc" for call in calls)
    assert wayrecord_bin_path.read_bytes() == WAYRECORD_BINARY


def test_installs_missing_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    calls = _command_fake(monkeypatch, installed=set())
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is True
    assert "ffmpeg" in (result.message or "")
    update_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "update"
    ]
    assert len(update_calls) == 1
    install_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "install"
    ]
    assert install_calls == [["apt-get", "install", "-y", "ffmpeg"]]


def test_skip_apt_update_skips_the_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    calls = _command_fake(monkeypatch, installed=set())
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path, skip_apt_update=True)
    )
    assert result.success is True
    assert result.changed is True
    update_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "update"
    ]
    assert update_calls == []


def test_install_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    _command_fake(monkeypatch, installed=set(), install_rc=1)
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is False
    assert "failed to install" in (result.error or "")


def test_wayrecord_built_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is True
    assert wayrecord_bin_path.read_bytes() == WAYRECORD_BINARY
    assert wayrecord_bin_path.stat().st_mode & 0o777 == 0o755
    assert "engine" in (result.message or "")


def test_wayrecord_idempotent_when_matching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    wayrecord_bin_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_bin_path.write_bytes(WAYRECORD_BINARY)
    wayrecord_bin_path.chmod(0o755)
    wayrecord_desktop_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_desktop_path.write_text(
        ffmpeg_setup._desktop_content(wayrecord_bin_path), encoding="utf-8"
    )
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"


def test_wayrecord_rebuilt_when_different(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    wayrecord_bin_path.parent.mkdir(parents=True, exist_ok=True)
    wayrecord_bin_path.write_bytes(b"old stale engine")
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    assert result.changed is True
    assert wayrecord_bin_path.read_bytes() == WAYRECORD_BINARY


def test_build_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    _command_fake(
        monkeypatch, installed=set(TEST_PACKAGES), build_rc=1
    )
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is False
    assert "cannot build wayrecord" in (result.error or "")


def test_desktop_written_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wayrecord_bin_path, wayrecord_desktop_path = _wayrecord_env(
        monkeypatch, tmp_path
    )
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is True
    expected = ffmpeg_setup._desktop_content(wayrecord_bin_path)
    assert wayrecord_desktop_path.read_text(encoding="utf-8") == expected
    assert "X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1" in expected
    assert "desktop entry" in (result.message or "")


def test_wayrecord_missing_template_is_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    template_dir = repo / "task_data" / "ffmpeg_setup"
    template_dir.mkdir(parents=True)
    monkeypatch.setattr(ffmpeg_setup, "REPO_ROOT", repo)
    wayrecord_bin_path = tmp_path / "bin" / "pyntara-wayrecord"
    wayrecord_desktop_path = tmp_path / "applications" / "pyntara-wayrecord.desktop"
    _command_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(
        _ctx(wayrecord_bin_path, wayrecord_desktop_path)
    )
    assert result.success is False
    assert "missing wayrecord source" in (result.error or "")
