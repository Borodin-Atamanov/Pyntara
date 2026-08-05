"""Unit tests for the run command: environment resolution and exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyntara import task_catalog, task_runner
from pyntara.config import CliToolsConfig, Config, ConfigError, EngineConfig
from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.pyntara import app, detect_default_mode

runner = CliRunner()


def _test_config(notice_timeout: int = 7) -> Config:
    """Config with values safe for unit tests; the real file is never touched."""

    return Config(
        engine=EngineConfig(
            task_data_root=Path("/tmp"),
            notice_timeout=notice_timeout,
            command_timeout_seconds=1800,
            process_check_timeout_seconds=5,
        ),
        cli_tools=CliToolsConfig(
            packages=("mc", "htop", "hollywood"),
            package_status_timeout_seconds=30,
            package_install_retries=3,
            package_success_threshold_percent=70,
        ),
    )


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "PYNTARA_INSTALL_MODE",
        "PYNTARA_TASKS",
        "PYNTARA_VAULT_PASSWORD",
        "PYNTARA_VAULT_SOURCE",
        "PYNTARA_FORCE_TASKS",
        "PYNTARA_SKIP_APT_UPDATE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_run_auto_detects_mode_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # A missing install mode is not an error: the engine auto-detects it,
    # reports the choice and runs with it (resilience rule).
    _clear_env(monkeypatch)
    monkeypatch.setattr("pyntara.pyntara.detect_default_mode", lambda timeout: "server")
    # All task modules are mocked as not implemented so no real dpkg or apt
    # command runs inside the unit test.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0  # unimplemented tasks are skipped, not failures
    assert "Install mode not set, using detected default: server" in result.output
    assert "Install mode: server" in result.output


def test_run_falls_back_to_detected_mode_on_unknown_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An install mode not in the configuration shows the resilience notice
    # and falls back to the auto-detected mode: the run continues.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "fancy")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0))
    monkeypatch.setattr("pyntara.pyntara.detect_default_mode", lambda timeout: "server")
    # All task modules are mocked as not implemented so no real dpkg or apt
    # command runs inside the unit test.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0  # unimplemented tasks are skipped, not failures
    assert (
        "Install mode 'fancy' was set through environment variables but not "
        "found in the configuration, applied mode 'server'"
    ) in result.output
    assert "Install mode: server" in result.output


def test_detect_default_mode_uses_desktop_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A desktop session variable means desktop.
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    monkeypatch.setattr("pyntara.pyntara._process_running", lambda name, timeout: False)
    assert detect_default_mode(5) == "server"
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert detect_default_mode(5) == "desktop"
    monkeypatch.delenv("XDG_CURRENT_DESKTOP")
    monkeypatch.setenv("DESKTOP_SESSION", "plasma")
    assert detect_default_mode(5) == "desktop"


def test_detect_default_mode_uses_desktop_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A running desktop process means desktop even without session variables.
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)

    def fake_running(name: str, timeout: float) -> bool:
        return name == "plasmashell"

    monkeypatch.setattr("pyntara.pyntara._process_running", fake_running)
    assert detect_default_mode(5) == "desktop"


def test_run_unknown_mode_countdown_has_no_unit_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The countdown counts seconds as plain numbers, without the letter s.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "fancy")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=2))
    monkeypatch.setattr("pyntara.pyntara.detect_default_mode", lambda timeout: "server")
    # All task modules are mocked as not implemented so no real dpkg or apt
    # command runs inside the unit test.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    slept: list[float] = []
    monkeypatch.setattr("pyntara.pyntara.time.sleep", lambda seconds: slept.append(seconds))
    result = runner.invoke(app, [])
    assert slept == [1.0, 1.0]
    assert "Execution continues in" in result.output
    assert "2s" not in result.output and "1s" not in result.output


def test_run_warns_and_continues_on_unknown_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unknown task name is not fatal: the engine shows an error notice,
    # pauses, then continues without the unknown name (simplified
    # architecture section 2).
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_TASKS", "nope")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0))
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "unknown task names in PYNTARA_TASKS" in result.output
    assert "All 0 tasks finished" in result.output


def test_run_pauses_on_invalid_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    # The notice must stay visible: the engine sleeps for the configured
    # timeout before continuing.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_TASKS", "nope")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=1))
    slept: list[float] = []
    monkeypatch.setattr("pyntara.pyntara.time.sleep", lambda seconds: slept.append(seconds))
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert slept == [1.0]


def test_run_skips_not_implemented_default_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # All task modules are mocked as not implemented: every default task is
    # reported as skipped and the command exits zero because nothing failed.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    for name in task_catalog.default_tasks("minimal"):
        assert f"[skip] {name}" in result.output


def test_run_reports_skipped_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # Skipped tasks are counted in the summary but do not fail the run.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")

    def fake_load(name: str) -> object:
        if name == "cli_tools":
            return lambda ctx: TaskResult(success=True, changed=True)
        return None

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "[done] cli_tools" in result.output
    assert "[skip] users" in result.output
    assert "Finished 1 of 6 tasks, skipped 5" in result.output


def test_run_resolves_selected_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    # PYNTARA_TASKS selects tasks; dependencies are resolved inside the engine.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "server")
    monkeypatch.setenv("PYNTARA_TASKS", "proxy_tunnel")
    result = runner.invoke(app, [])
    assert "Tasks: proxy_server proxy_tunnel" in result.output


def test_run_warns_and_continues_on_unknown_force_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo in the force list shows a notice and the run continues with the
    # remaining tasks.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "hostnam")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0))

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: hostnam" in result.output


def test_run_warns_and_continues_on_force_tasks_outside_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Forcing a task that would never run is a notice, not a stop: the run
    # continues without the invalid entry.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "apps")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0))

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: apps" in result.output
    assert "Force:" not in result.output


def test_run_reports_force_tasks_in_the_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A valid force list is reported and does not change the task set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "hostname users")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Force: hostname users" in result.output


def test_run_reports_success_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # When every task succeeds, the run reports the count and exits 0.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True, message="done")

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    expected = len(task_catalog.default_tasks("minimal"))
    assert f"All {expected} tasks finished" in result.output


def _captured_skip_flag(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> bool | None:
    """Run the engine with PYNTARA_SKIP_APT_UPDATE set to value and return
    the flag that reached Context through run_tasks."""

    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    if value is None:
        monkeypatch.delenv("PYNTARA_SKIP_APT_UPDATE", raising=False)
    else:
        monkeypatch.setenv("PYNTARA_SKIP_APT_UPDATE", value)
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )
    captured: dict[str, bool | None] = {"flag": None}

    def fake_run_tasks(ctx: Context, names: list[str]) -> list[tuple[str, TaskResult]]:
        captured["flag"] = ctx.skip_apt_update
        return []

    monkeypatch.setattr("pyntara.pyntara.run_tasks", fake_run_tasks)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    return captured["flag"]


def test_run_skip_apt_update_true_reaches_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PYNTARA_SKIP_APT_UPDATE=1 flows from the environment into Context.
    assert _captured_skip_flag(monkeypatch, "1") is True


def test_run_skip_apt_update_false_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without the variable the flag stays False, so the index refresh runs.
    assert _captured_skip_flag(monkeypatch, None) is False


def test_run_skip_apt_update_zero_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit 0 must not enable the flag; only 1, true or yes do.
    assert _captured_skip_flag(monkeypatch, "0") is False


def test_run_fails_when_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # The config file is mandatory: without it the engine cannot know what
    # to provision, so the run stops with an error and a nonzero exit code.
    def missing_config(path: Path) -> Config:
        raise ConfigError("config file not found: config.toml")

    _clear_env(monkeypatch)
    monkeypatch.setattr("pyntara.pyntara.load_config", missing_config)
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "config file not found" in result.output
