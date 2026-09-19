"""Unit tests for the run command: environment resolution and exit codes."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyntara import task_catalog, task_runner
from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.pyntara import (
    _process_running,
    _run_context,
    app,
    detect_default_mode,
)
from pyntara.values import engine as engine_values
from pyntara.values import tasks as tasks_values

runner = CliRunner()

# The real catalog from the values package; the run tests use it so the
# default task sets the app resolves are the actual ones.
REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = tasks_values.CATALOG

# Process names that mark a desktop session in the mode detection tests.
DEFAULT_DESKTOP_PROCESSES = ("kwin_wayland", "kwin_x11", "plasmashell", "gnome-shell")


@pytest.fixture(autouse=True)
def _point_the_run_at_the_test_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every run of this file free of the two declared pauses.

    A run waits for the notice timeout when it reports a problem and sleeps
    between two tasks; a test that asserts a pause sets its own value.
    """

    monkeypatch.setattr(engine_values, "NOTICE_TIMEOUT", 0)
    monkeypatch.setattr(engine_values, "TASK_START_DELAY_SECONDS", 0)


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


@pytest.fixture(autouse=True)
def _no_live_desktop_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the unit tests away from the live desktop session.

    The run command reads the session environment of the configured desktop
    user and exports it into the environment of the process. A unit test must
    never ask the real session manager for it, so the resolver reports no
    session unless a test replaces this fixture.
    """

    monkeypatch.setattr(
        "pyntara.pyntara.session_environment", lambda username, **kwargs: {}
    )


@pytest.fixture(autouse=True)
def _fixed_desktop_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the resolved desktop account, so a run test needs no logged-in user.

    The run resolves the desktop account of the machine before the tasks; a
    unit test must never depend on who is logged in, so the resolver reports
    the shipped pair unless a test replaces this fixture.
    """

    monkeypatch.setattr(
        "pyntara.pyntara.get_desktop_username_and_home",
        lambda: ("i", "/home/i"),
    )


def test_run_exports_the_desktop_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # The run hands the session variables of the desktop user to the process,
    # so every task and every child process inherits them no matter where the
    # run itself was started.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    monkeypatch.setattr(
        "pyntara.pyntara.session_environment",
        lambda username, **kwargs: {
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            "WAYLAND_DISPLAY": "wayland-0",
        },
    )
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Desktop session of i exported" in result.output
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"


def test_run_reports_a_missing_desktop_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without a live session the run says so and continues: the desktop tasks
    # then write their values and report that they apply at the next login.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "No live desktop session for i" in result.output


def test_run_skips_the_export_without_a_desktop_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A config that names no desktop user leaves the export out and reports
    # it; the run itself continues.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "No live desktop session for i" in result.output


def test_run_auto_detects_mode_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # A missing install mode is not an error: the engine auto-detects it,
    # reports the choice and runs with it (resilience rule).
    _clear_env(monkeypatch)
    monkeypatch.setattr(
        "pyntara.pyntara.detect_default_mode",
        lambda: "server",
    )
    # All task modules are mocked as not implemented so no real dpkg or apt
    # command runs inside the unit test.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0  # unimplemented tasks are skipped, not failures
    assert "Install mode not set, using detected default: server" in result.output
    assert "Install mode: server" in result.output


def test_run_warns_and_fails_when_the_mode_names_no_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A written mode that names no declared mode shows the resilience notice,
    # which names the declared modes, and the run continues on the
    # auto-detected mode; the substitution is a warning of the run, so a mode
    # that became another mode never passes as the one that was asked for.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "fancy")
    monkeypatch.setattr(
        "pyntara.pyntara.detect_default_mode",
        lambda: "server",
    )
    # All task modules are mocked as not implemented so no real dpkg or apt
    # command runs inside the unit test.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "names no declared mode, applied mode 'server'" in result.output
    assert (
        "The declared modes are: minimal, server, desktop, fast_desktop"
        in result.output
    )
    assert "Install mode: server" in result.output
    assert "[warn] run: install mode 'fancy' names no declared mode" in result.output


def test_run_applies_a_mode_name_written_with_another_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A person writes the mode into the file by hand, so a hyphen instead of
    # the underscore of the catalog must select the mode and not drop the run
    # onto the auto-detected one.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "Fast-Desktop")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Install mode 'Fast-Desktop' applied as 'fast_desktop'" in result.output
    assert "Install mode: fast_desktop" in result.output


def test_run_warns_and_fails_when_the_default_vault_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Whatever reason led the installer to the default vault, the run reports
    # it: on a machine that is already configured the runtime secrets are rebuilt
    # from the repository test vault.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "server")
    monkeypatch.setenv("PYNTARA_VAULT_SOURCE", "default")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "[warn] run: the run takes its secrets from the default vault" in result.output


def test_run_stays_silent_for_the_production_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "server")
    monkeypatch.setenv("PYNTARA_VAULT_SOURCE", "production")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "[warn] run:" not in result.output


def test_detect_default_mode_uses_desktop_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A desktop session variable means desktop.
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    monkeypatch.setattr(
        "pyntara.pyntara._process_running",
        lambda name, timeout: False,
    )
    assert detect_default_mode() == "server"
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert detect_default_mode() == "desktop"
    monkeypatch.delenv("XDG_CURRENT_DESKTOP")
    monkeypatch.setenv("DESKTOP_SESSION", "plasma")
    assert detect_default_mode() == "desktop"


def test_detect_default_mode_uses_desktop_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A running desktop process means desktop even without session variables.
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)

    def fake_running(name: str, timeout: float) -> bool:
        return name == "plasmashell"

    monkeypatch.setattr("pyntara.pyntara._process_running", fake_running)
    assert detect_default_mode() == "desktop"


def test_process_check_uses_the_configured_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The query is the configured [engine] process_check_command with the
    # process name filled in: another tool and another flag produce the argv
    # the check runs, and the exit status alone answers.
    seen: list[list[str]] = []

    class FakeResult:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> FakeResult:
        seen.append(command)
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        engine_values, "PROCESS_CHECK_COMMAND", ("pidof", "-x", "{process_name}")
    )
    assert _process_running("plasmashell", 5) is True
    assert seen == [["/usr/bin/pidof", "-x", "plasmashell"]]


def test_process_check_without_the_tool_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Best effort: a machine without the configured tool reports no running
    # process instead of stopping the run, and the default mode is server.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _process_running("plasmashell", 5) is False


def test_run_unknown_mode_countdown_has_no_unit_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The countdown counts seconds as plain numbers, without the letter s.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "fancy")
    monkeypatch.setattr(
        "pyntara.pyntara.detect_default_mode",
        lambda: "server",
    )
    # All task modules are mocked as not implemented so no real dpkg or apt
    # command runs inside the unit test.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    slept: list[float] = []
    monkeypatch.setattr(
        "pyntara.pyntara.time.sleep", lambda seconds: slept.append(seconds)
    )
    monkeypatch.setattr(engine_values, "NOTICE_TIMEOUT", 2)
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
    slept: list[float] = []
    monkeypatch.setattr(
        "pyntara.pyntara.time.sleep", lambda seconds: slept.append(seconds)
    )
    monkeypatch.setattr(engine_values, "NOTICE_TIMEOUT", 1)
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

    def fake_load(name: str) -> object:
        if name == "cli_tools_lite_setup":
            return lambda ctx: TaskResult(success=True, changed=True)
        return None

    monkeypatch.setattr(task_runner, "load_task", fake_load)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "[done] cli_tools_lite_setup" in result.output
    assert "[skip] add_extra_repos" in result.output
    expected = len(_default_run_set("minimal"))
    assert f"Finished 1 of {expected} tasks, skipped {expected - 1}" in result.output


def test_run_configures_the_journal_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The logger writes with the declared journal identifier, and the run
    # hands it over before the first message, so the engine announces itself
    # under the declared name.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    configured: list[str] = []
    monkeypatch.setattr("pyntara.pyntara.configure_journal", configured.append)
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert configured
    assert configured[-1] == engine_values.JOURNAL_IDENTIFIER


def test_run_journals_the_declared_identifier_without_a_config_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The run reads no config file at all, and the journal identifier is a
    # declared value, so the run announces itself under it on every machine.
    _clear_env(monkeypatch)
    configured: list[str] = []
    monkeypatch.setattr("pyntara.pyntara.configure_journal", configured.append)
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert configured
    assert configured[-1] == engine_values.JOURNAL_IDENTIFIER


def test_run_resolves_selected_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    # PYNTARA_TASKS selects tasks; dependencies are resolved inside the engine.
    # cli_tools_lite_setup pulls add_extra_repos in first.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "server")
    monkeypatch.setenv("PYNTARA_TASKS", "cli_tools_lite_setup")
    # The task module is mocked away so no real dpkg or apt command runs.
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert "Tasks: add_extra_repos cli_tools_lite_setup" in result.output


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
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    run_set = _default_run_set("minimal")
    assert f"Tasks: {' '.join(run_set)}" in result.output
    assert run_set.index("nextdns_setup_system_wide") < run_set.index("dnsproxy_setup")


def test_run_warns_and_continues_on_unknown_force_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo in the force list shows a notice and the run continues with the
    # remaining tasks.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "nope")

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
    # continues without the invalid entry. cli_tools_lite_setup is a known task that is
    # not part of the narrowed run set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_TASKS", "add_extra_repos")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "cli_tools_lite_setup")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: cli_tools_lite_setup" in result.output
    assert "Force:" not in result.output


def test_run_reports_force_tasks_in_the_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A valid force list is reported and does not change the task set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "add_extra_repos cli_tools_lite_setup")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Force: add_extra_repos cli_tools_lite_setup" in result.output


def test_run_force_all_reports_the_full_run_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The keyword all forces every task of the run set, not every catalog
    # task: the Force line lists exactly the resolved minimal run set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "all")

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

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    expected = " ".join(sorted(_default_run_set("minimal")))
    assert f"Force: {expected}" in result.output


def test_the_force_all_keyword_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The word that forces the whole run set is a declared value: with
    # another word another keyword forces everything, while the shipped one
    # becomes a name the catalog does not know.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "every")
    shipped_keyword = engine_values.FORCE_ALL_KEYWORD
    monkeypatch.setattr(engine_values, "FORCE_ALL_KEYWORD", "every")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    expected = " ".join(sorted(_default_run_set("minimal")))
    assert f"Force: {expected}" in result.output

    monkeypatch.setattr(engine_values, "FORCE_ALL_KEYWORD", shipped_keyword)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names in PYNTARA_FORCE_TASKS: every" in result.output
    assert "Force:" not in result.output


def test_the_run_context_carries_the_clone_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every task reads the shipped templates under task_data/ through
    # ctx.repo_root, which the composition root fills with the one
    # computation of the clone root. A wrong depth would reach every task
    # at once and pass the suite while failing on the machine, which is
    # what happened before this proof existed, so the root is compared
    # with the clone the tests run from and both directories are required.
    _clear_env(monkeypatch)
    ctx = _run_context("minimal", ["hostname"])
    assert ctx.repo_root == REPO_ROOT
    assert (ctx.repo_root / "task_data").is_dir()
    assert not (ctx.repo_root / "task_data").is_relative_to(ctx.repo_root / "src")


def test_run_force_all_still_reports_invalid_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Validation stays unconditional: a typo next to all shows the notice,
    # and all still forces the whole run set.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "all nope")

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
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", "CLI_TOOLS_LITE_SETUP")

    def ok_task(ctx: object) -> TaskResult:
        return TaskResult(success=True)

    monkeypatch.setattr(task_runner, "load_task", lambda name: ok_task)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "invalid task names" not in result.output
    assert "Force: cli_tools_lite_setup" in result.output


def test_run_tasks_match_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PYNTARA_TASKS entries are matched case-insensitively; the run set
    # carries the canonical catalog names.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "server")
    monkeypatch.setenv("PYNTARA_TASKS", "CLI_TOOLS_LITE_SETUP")
    monkeypatch.setattr(task_runner, "load_task", lambda name: None)
    result = runner.invoke(app, [])
    assert "Tasks: add_extra_repos cli_tools_lite_setup" in result.output


def _captured_force_tasks(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> frozenset[str]:
    """Run the engine with PYNTARA_FORCE_TASKS set to value and return the
    set that reached Context through run_tasks."""

    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_FORCE_TASKS", value)
    captured: dict[str, frozenset[str]] = {"force_tasks": frozenset()}

    def fake_run_tasks(ctx: Context, names: list[str]) -> list[tuple[str, TaskResult]]:
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


def test_the_true_answers_of_the_flag_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The answers that mean true are declared values: with another list
    # another answer enables the flag, while the shipped ones no longer do.
    _clear_env(monkeypatch)
    monkeypatch.setenv("PYNTARA_INSTALL_MODE", "minimal")
    monkeypatch.setenv("PYNTARA_SKIP_APT_UPDATE", "aye")
    monkeypatch.setattr(engine_values, "ENVIRONMENT_FLAG_TRUE_VALUES", ("aye", "si"))
    captured: dict[str, bool | None] = {"flag": None}

    def fake_run_tasks(ctx: Context, names: list[str]) -> list[tuple[str, TaskResult]]:
        captured["flag"] = ctx.skip_apt_update
        return []

    monkeypatch.setattr("pyntara.pyntara.run_tasks", fake_run_tasks)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert captured["flag"] is True

    monkeypatch.setenv("PYNTARA_SKIP_APT_UPDATE", "yes")
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert captured["flag"] is False


def test_run_skip_apt_update_zero_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit 0 must not enable the flag; only 1, true or yes do.
    assert _captured_skip_flag(monkeypatch, "0") is False


def test_run_reports_when_the_task_catalog_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty catalog leaves nothing to run. The run reports the state and
    # exits nonzero instead of crashing or claiming success for a machine it
    # could not provision (architecture contract, Configuration).
    _clear_env(monkeypatch)
    monkeypatch.setattr("pyntara.pyntara.tasks_values.CATALOG", ())
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "the task catalog is empty" in result.output
