"""Unit tests for the playwright_setup task.

All external resources (dpkg-query, apt-get, runuser, npm) are mocked via
monkeypatch of subprocess.run; the tests never touch the real system or
the real npm registry (docs/guides/developer-guide.md).
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
from pyntara.tasks import playwright_setup

# The version the fake playwright-cli --version probe reports.
VERSION = "1.2.3"

# The real catalog from the repository config; the mode-membership and
# dependency tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks


def _test_config(tmp_path: Path) -> Config:
    """Config whose user home lives in the tmp tree."""

    return make_config(playwright_setup_home_dir=str(tmp_path / "home"))


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    return make_context(
        task_name="playwright_setup",
        install_mode="desktop",
        config=_test_config(tmp_path),
        force_tasks=frozenset({"playwright_setup"}) if force else frozenset(),
    )


def _cli_bin(cfg: Config) -> Path:
    return (
        Path(cfg.playwright_setup.home_dir) / ".local" / "bin" / "playwright-cli"
    )


def _fake_run_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dpkg_installed: bool = True,
    npm_rc: int = 0,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query reports the configured package state, a runuser command
    that carries --version answers the version probe, and a runuser npm
    install creates the playwright-cli binary under its --prefix so the
    read-back after the install succeeds. A nonzero npm_rc makes the npm
    install raise, which stands for a failed install.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            status = "install ok installed" if dpkg_installed else "deinstall ok config-files"
            return _FakeProc(0, stdout=status)
        if command[0] == "runuser":
            if "--version" in command:
                return _FakeProc(0, stdout=VERSION)
            if npm_rc != 0 and kwargs.get("check", True):
                raise subprocess.CalledProcessError(npm_rc, command)
            prefix_index = command.index("--prefix") + 1
            binary = Path(command[prefix_index]) / "bin" / "playwright-cli"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#! /usr/bin/env node\n", encoding="utf-8")
            binary.chmod(0o755)
            return _FakeProc(npm_rc, "")
        return _FakeProc(0, "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_playwright_setup_is_in_desktop_default_set() -> None:
    assert "playwright_setup" in task_catalog.default_tasks("desktop", REAL_TASKS)
    assert "playwright_setup" not in task_catalog.default_tasks("minimal", REAL_TASKS)
    assert "playwright_setup" not in task_catalog.default_tasks("server", REAL_TASKS)


def test_playwright_setup_depends_on_browser_and_universe() -> None:
    # The resolved run set lists add_extra_repos and chrome_setup before
    # playwright_setup, so the universe component and the CDP browser entry
    # are in place before the tool install.
    resolved = task_catalog.resolve(["playwright_setup"], REAL_TASKS)
    assert resolved.index("add_extra_repos") < resolved.index("playwright_setup")
    assert resolved.index("chrome_setup") < resolved.index("playwright_setup")


def test_real_config_names_the_desktop_user_and_the_cli_package() -> None:
    config = load_config(REPO_ROOT / "config")
    assert config.playwright_setup.username == "i"
    assert config.playwright_setup.home_dir == "/home/i"
    assert config.playwright_setup.cli_package == "@playwright/cli"
    assert "nodejs" in config.playwright_setup.packages


def test_cli_version_is_empty_when_the_binary_is_missing(tmp_path: Path) -> None:
    config = _test_config(tmp_path)
    assert playwright_setup._cli_version(config.playwright_setup, timeout=5) == ""


def test_already_installed_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run_factory(monkeypatch)
    config = _test_config(tmp_path)
    binary = _cli_bin(config)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#! /usr/bin/env node\n", encoding="utf-8")
    binary.chmod(0o755)
    result = playwright_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert VERSION in (result.message or "")
    assert "already installed" in (result.message or "")
    # No npm install ran.
    assert not any("install" in command for command in calls)


def test_installs_playwright_cli_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run_factory(monkeypatch)
    config = _test_config(tmp_path)
    result = playwright_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert VERSION in (result.message or "")
    home = Path(config.playwright_setup.home_dir)
    npm_call = [
        "runuser",
        "-u",
        "i",
        "--",
        "env",
        f"HOME={home}",
        "npm",
        "install",
        "-g",
        "@playwright/cli",
        "--prefix",
        str(home / ".local"),
    ]
    assert npm_call in calls
    assert _cli_bin(config).is_file()


def test_force_reinstalls_even_when_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run_factory(monkeypatch)
    config = _test_config(tmp_path)
    binary = _cli_bin(config)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#! /usr/bin/env node\n", encoding="utf-8")
    binary.chmod(0o755)
    result = playwright_setup.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert result.changed is True
    assert any("install" in command and "--prefix" in command for command in calls)


def test_npm_install_failure_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_run_factory(monkeypatch, npm_rc=2)
    result = playwright_setup.task(_ctx(tmp_path))
    assert result.success is False
    assert "playwright-cli install failed" in (result.error or "")
