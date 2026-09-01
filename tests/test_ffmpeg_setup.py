"""Unit tests for the ffmpeg_setup task.

All external resources (dpkg-query, apt-get) are mocked via monkeypatch;
the tests never touch the real system (docs/guides/developer-guide.md).
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


def _test_config() -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    return make_config(ffmpeg_setup_packages=TEST_PACKAGES)


def _ctx(*, skip_apt_update: bool = False) -> Context:
    return make_context(config=_test_config(), skip_apt_update=skip_apt_update)


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: set[str],
    install_rc: int = 0,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query answers from the installed set, apt-get install answers
    with install_rc and every other command succeeds; all calls are
    recorded. A nonzero return with check=True raises exactly like the
    real subprocess.run, so install failures surface as exceptions.
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


def test_all_installed_skips_apt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake(monkeypatch, installed=set(TEST_PACKAGES))
    result = ffmpeg_setup.task(_ctx())
    assert result.success is True
    assert result.changed is False
    assert result.message == "already installed"
    assert not any(call[0] == "apt-get" for call in calls)


def test_installs_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake(monkeypatch, installed=set())
    result = ffmpeg_setup.task(_ctx())
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake(monkeypatch, installed=set())
    result = ffmpeg_setup.task(_ctx(skip_apt_update=True))
    assert result.success is True
    assert result.changed is True
    update_calls = [
        call for call in calls if call[0] == "apt-get" and call[1] == "update"
    ]
    assert update_calls == []


def test_install_failure_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake(monkeypatch, installed=set(), install_rc=1)
    result = ffmpeg_setup.task(_ctx())
    assert result.success is False
    assert "failed to install" in (result.error or "")
