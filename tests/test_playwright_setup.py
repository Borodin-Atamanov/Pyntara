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
from support import make_context

from pyntara import task_catalog
from pyntara.config import load_config
from pyntara.context import Context
from pyntara.tasks import playwright_setup
from pyntara.values import common as common_values
from pyntara.values import playwright_setup as playwright_values

# The version the fake playwright-cli --version probe reports.
VERSION = "1.2.3"

# The real catalog from the repository config; the mode-membership and
# dependency tests use it so they cover the actual task set.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks


def _use_playwright_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Point the task values at a home directory inside the tmp tree.

    The values are module constants, so the helper patches the module for
    the test that calls it; monkeypatch puts the shipped values back
    afterwards, whether the test passed or failed.
    """

    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(tmp_path / "home"))


def _ctx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, force: bool = False
) -> Context:
    """Context of the task with its values pointed at the tmp tree."""

    _use_playwright_values(monkeypatch, tmp_path)
    return make_context(
        task_name="playwright_setup",
        install_mode="desktop",
        force_tasks=frozenset({"playwright_setup"}) if force else frozenset(),
    )


def _cli_bin() -> Path:
    """The playwright-cli binary path the values module names."""

    return (
        Path(common_values.DESKTOP_HOME_DIR)
        / playwright_values.USER_PREFIX_RELATIVE_PATH
        / playwright_values.CLI_BIN_RELATIVE_PATH
    )


def _fake_run_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dpkg_installed: bool = True,
    npm_rc: int = 0,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    dpkg-query reports the configured package state, a command that carries
    --version answers the version probe whatever prefix runs it, and a
    command that carries --prefix creates the playwright-cli binary under
    that prefix so the read-back after the install succeeds. A nonzero
    npm_rc makes the install raise, which stands for a failed install.
    """

    binary_rel = playwright_values.CLI_BIN_RELATIVE_PATH
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "dpkg-query":
            status = "install ok installed" if dpkg_installed else "deinstall ok config-files"
            return _FakeProc(0, stdout=status)
        if "--version" in command:
            return _FakeProc(0, stdout=VERSION)
        if "--prefix" in command:
            if npm_rc != 0 and kwargs.get("check", True):
                raise subprocess.CalledProcessError(npm_rc, command)
            prefix_index = command.index("--prefix") + 1
            binary = Path(command[prefix_index]) / binary_rel
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


def test_the_shipped_values_name_the_desktop_user_and_the_cli_package() -> None:
    assert common_values.DESKTOP_USERNAME == "i"
    assert common_values.DESKTOP_HOME_DIR == "/home/i"
    assert playwright_values.CLI_PACKAGE == "@playwright/cli"
    assert "nodejs" in playwright_values.PACKAGES


def test_cli_version_is_empty_when_the_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_playwright_values(monkeypatch, tmp_path)
    assert playwright_setup._cli_version(timeout=5) == ""


def test_already_installed_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run_factory(monkeypatch)
    ctx = _ctx(monkeypatch, tmp_path)
    binary = _cli_bin()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#! /usr/bin/env node\n", encoding="utf-8")
    binary.chmod(0o755)
    result = playwright_setup.task(ctx)
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
    result = playwright_setup.task(_ctx(monkeypatch, tmp_path))
    assert result.success is True
    assert result.changed is True
    assert VERSION in (result.message or "")
    home = Path(common_values.DESKTOP_HOME_DIR)
    npm_call = [
        "runuser",
        "-u",
        common_values.DESKTOP_USERNAME,
        "--",
        "env",
        f"HOME={common_values.DESKTOP_HOME_DIR}",
        "npm",
        "install",
        "-g",
        playwright_values.CLI_PACKAGE,
        "--prefix",
        str(home / playwright_values.USER_PREFIX_RELATIVE_PATH),
    ]
    assert npm_call in calls
    assert _cli_bin().is_file()


def test_runuser_command_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another runuser template in the values is the prefix the task runs, so
    # the command shape is not a value of the module.
    calls = _fake_run_factory(monkeypatch)
    ctx = _ctx(monkeypatch, tmp_path)
    monkeypatch.setattr(
        playwright_values,
        "RUNUSER_COMMAND",
        ("sudo", "-u", "{username}", "env"),
    )
    result = playwright_setup.task(ctx)
    assert result.success is True
    assert any(command[:4] == ["sudo", "-u", "i", "env"] for command in calls)


def test_force_reinstalls_even_when_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_run_factory(monkeypatch)
    ctx = _ctx(monkeypatch, tmp_path, force=True)
    binary = _cli_bin()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#! /usr/bin/env node\n", encoding="utf-8")
    binary.chmod(0o755)
    result = playwright_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any("install" in command and "--prefix" in command for command in calls)


def test_npm_install_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed npm install is a recoverable failure: the task completes, so
    # the remaining tasks of the run still do their work, and the reason
    # reaches the installer as a warning of this task.
    _fake_run_factory(monkeypatch, npm_rc=2)
    result = playwright_setup.task(_ctx(monkeypatch, tmp_path))
    assert result.success is True
    assert result.warnings
    assert any(
        "playwright-cli install failed" in warning for warning in result.warnings
    )


def test_missing_runtime_packages_stop_the_task_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # nodejs and npm are the mechanism of this task: without them npm can
    # install nothing, so the task stops its own remaining steps, still
    # reports a completed task, and names the packages it could not install
    # in a warning instead of failing the run.
    def failing_install(*args: object, **kwargs: object) -> tuple[
        list[str], list[tuple[str, str]], list[str]
    ]:
        del args, kwargs
        return [], [("nodejs", "no candidate")], []

    monkeypatch.setattr(
        "pyntara.tasks.playwright_setup.package_is_installed",
        lambda engine, package, timeout: False,
    )
    monkeypatch.setattr(
        "pyntara.tasks.playwright_setup.install_packages", failing_install
    )
    result = playwright_setup.task(_ctx(monkeypatch, tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings == ("cannot install nodejs and npm: nodejs: no candidate",)
