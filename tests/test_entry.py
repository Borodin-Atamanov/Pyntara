"""Unit tests for the run command: environment resolution and exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest
from support import make_config
from typer.testing import CliRunner

from pyntara import task_catalog, task_runner
from pyntara.config import Config, ConfigError, load_config
from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.pyntara import app, detect_default_mode

runner = CliRunner()

# The real catalog from the repository config; the run tests use it so the
# mocked Config matches the actual default task sets and the app output.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks

# Process names that mark a desktop session in the mode detection tests.
DEFAULT_DESKTOP_PROCESSES = ("kwin_wayland", "kwin_x11", "plasmashell", "gnome-shell")


def _test_config(notice_timeout: int = 7) -> Config:
    """Config with values safe for unit tests; the real file is never touched.

    task_start_delay_seconds is zeroed so a run over the full task set does
    not sleep half a second per task (14 tasks would add 7 seconds).
    """

    return make_config(
        notice_timeout=notice_timeout,
        task_start_delay_seconds=0,
        cli_tools_packages=("mc", "htop", "hollywood"),
        tasks=REAL_TASKS,
    )


def _default_run_set(mode: str) -> list[str]:
    """Resolved default run set for a mode: mode defaults plus catalog
    dependencies, in run order."""

    return task_catalog.resolve(
        task_catalog.default_tasks(mode, REAL_TASKS), REAL_TASKS
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
    monkeypatch.setattr(
        "pyntara.pyntara.detect_default_mode",
        lambda timeout, processes: "server",
    )
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
    monkeypatch.setattr(
        "pyntara.pyntara.detect_default_mode",
        lambda timeout, processes: "server",
    )
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
    assert detect_default_mode(5, DEFAULT_DESKTOP_PROCESSES) == "server"
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert detect_default_mode(5, DEFAULT_DESKTOP_PROCESSES) == "desktop"
    monkeypatch.delenv("XDG_CURRENT_DESKTOP")
    monkeypatch.setenv("DESKTOP_SESSION", "plasma")
    assert detect_default_mode(5, DEFAULT_DESKTOP_PROCESSES) == "desktop"


def test_detect_default_mode_uses_desktop_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A running desktop process means desktop even without session variables.
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)

    def fake_running(name: str, timeout: float) -> bool:
        return name == "plasmashell"

    monkeypatch.setattr("pyntara.pyntara._process_running", fake_running)
    assert detect_default_mode(5, DEFAULT_DESKTOP_PROCESSES) == "desktop"


def test_run_unknown_mode_countdown_has_no_unit_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The countdown counts seconds as plain numbers, without the letter s.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "fancy")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=2))
    monkeypatch.setattr(
        "pyntara.pyntara.detect_default_mode",
        lambda timeout, processes: "server",
    )
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
    # architecture, What changed).
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
    # All task modules are mocked as not implemented: every task of the
    # default run set is reported as skipped and the command exits zero
    # because nothing failed.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    for name in _default_run_set("minimal"):
        assert f"[skip] {name}" in result.output


def test_run_reports_skipped_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # Skipped tasks are counted in the summary but do not fail the run. The
    # expected counts are derived from the real catalog so the test stays
    # correct when the catalog grows or shrinks.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )

    def fake_load(name: str) -> object:
        if name == "cli_tools":
            return lambda ctx: TaskResult(success=True, changed=True)
        return None

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "[done] cli_tools" in result.output
    assert "[skip] add_extra_repos" in result.output
    expected = len(_default_run_set("minimal"))
    assert (
        f"Finished 1 of {expected} tasks, skipped {expected - 1}"
        in result.output
    )


def test_run_resolves_selected_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    # PYNTARA_TASKS selects tasks; dependencies are resolved inside the engine.
    # cli_tools pulls add_extra_repos in first.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "server")
    monkeypatch.setenv("PYNTARA_TASKS", "cli_tools")
    # The task module is mocked away so no real dpkg or apt command runs.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert "Tasks: add_extra_repos cli_tools" in result.output


def test_run_default_run_set_resolves_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mode defaults are resolved like an explicit selection: dnsproxy_setup
    # belongs to the minimal defaults and depends on
    # nextdns_setup_system_wide, which belongs to no mode. The dependency
    # must appear in the default run set before dnsproxy_setup, so the
    # profile id file exists before dnsproxy runs.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    run_set = _default_run_set("minimal")
    assert f"Tasks: {' '.join(run_set)}" in result.output
    assert run_set.index("nextdns_setup_system_wide") < run_set.index(
        "dnsproxy_setup"
    )


def test_run_warns_and_continues_on_unknown_force_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo in the force list shows a notice and the run continues with the
    # remaining tasks.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "nope")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0))

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: nope" in result.output


def test_run_warns_and_continues_on_force_tasks_outside_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Forcing a task that would never run is a notice, not a stop: the run
    # continues without the invalid entry. cli_tools is a known task that is
    # not part of the narrowed run set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_TASKS", "add_extra_repos")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "cli_tools")
    monkeypatch.setattr("pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0))

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: cli_tools" in result.output
    assert "Force:" not in result.output


def test_run_reports_force_tasks_in_the_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A valid force list is reported and does not change the task set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "add_extra_repos cli_tools")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Force: add_extra_repos cli_tools" in result.output


def test_run_force_all_reports_the_full_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The keyword all forces every task of the run set, not every catalog
    # task: the Force line lists exactly the resolved minimal run set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "all")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    expected = " ".join(sorted(_default_run_set("minimal")))
    assert f"Force: {expected}" in result.output


def test_run_force_all_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The keyword all matches in any case and forces the whole run set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "ALL")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    expected = " ".join(sorted(_default_run_set("minimal")))
    assert f"Force: {expected}" in result.output


def test_run_force_all_still_reports_invalid_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Validation stays unconditional: a typo next to all shows the notice,
    # and all still forces the whole run set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "all nope")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: nope" in result.output
    expected = " ".join(sorted(_default_run_set("minimal")))
    assert f"Force: {expected}" in result.output


def test_run_force_tasks_match_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A force entry in another case resolves to the canonical catalog name,
    # so the task's own lowercase check still matches.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "CLI_TOOLS")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names" not in result.output
    assert "Force: cli_tools" in result.output


def test_run_tasks_match_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PYNTARA_TASKS entries are matched case-insensitively; the run set
    # carries the canonical catalog names.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "server")
    monkeypatch.setenv("PYNTARA_TASKS", "CLI_TOOLS")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert "Tasks: add_extra_repos cli_tools" in result.output


def _captured_force_tasks(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> frozenset[str]:
    """Run the engine with PYNTARA_FORCE_TASKS set to value and return the
    set that reached Context through run_tasks."""

    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", value)
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )
    captured: dict[str, frozenset[str]] = {"force_tasks": frozenset()}

    def fake_run_tasks(
        ctx: Context, names: list[str]
    ) -> list[tuple[str, TaskResult]]:
        captured["force_tasks"] = ctx.force_tasks
        return []

    monkeypatch.setattr("pyntara.pyntara.run_tasks", fake_run_tasks)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    return captured["force_tasks"]


def test_run_force_all_reaches_context_as_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # all expands to exactly the resolved run set: forced and selected are
    # the same set, so every selected task reruns.
    expected = frozenset(_default_run_set("minimal"))
    assert _captured_force_tasks(monkeypatch, "all") == expected


def test_run_reports_success_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # When every task succeeds, the run reports the count and exits 0.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True, message="done")

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    expected = len(_default_run_set("minimal"))
    assert f"All {expected} tasks finished" in result.output


def test_run_reports_warnings_and_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tasks that completed with warnings make the run exit nonzero and
    # report the count, so scripts can detect an incomplete configuration.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(
        "pyntara.pyntara.load_config", lambda path: _test_config(notice_timeout=0)
    )

    def warn_task(ctx: object) -> TaskResult:
        return TaskResult(
            success=True, message="done", warnings=("cannot apply hotkey",)
        )

    monkeypatch.setattr(task_runner, "load_task", lambda name: warn_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    expected = len(_default_run_set("minimal"))
    assert (
        f"Finished {expected} of {expected} tasks, {expected} with warnings"
        in result.output
    )


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
