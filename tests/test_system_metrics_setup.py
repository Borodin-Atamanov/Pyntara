"""Unit tests for the system_metrics_setup task.

All external resources (uv, the venv python, systemctl, filesystem paths)
are mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The unit and command templates are
rendered from fixtures, so the tests never read the repository templates.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from string import Template
from typing import TypedDict

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import __version__
from pyntara.context import Context
from pyntara.tasks import system_metrics_setup
from pyntara.utils import substituted_command
from pyntara.values import engine as engine_values
from pyntara.values import system_metrics_setup as values

UNIT_TEMPLATE = """\
[Unit]
Description=System Metrics service
# Deployed by Pyntara $version
After=local-fs.target

[Service]
Type=simple
StandardOutput=null
Restart=on-failure
$exec_lines

[Install]
WantedBy=multi-user.target
"""

INGEST_SERVICE_TEMPLATE = """\
[Unit]
Description=System Metrics spool ingest
# Deployed by Pyntara $version
After=local-fs.target

[Service]
Type=oneshot
$exec_lines
"""

INGEST_PATH_TEMPLATE = """\
[Unit]
Description=Watch the System Metrics spool directory
# Deployed by Pyntara $version
After=local-fs.target

[Path]
PathChanged=$spool_dir

[Install]
WantedBy=multi-user.target
"""

COLLECTOR_SERVICE_TEMPLATE = """\
[Unit]
Description=System Metrics report collector
# Deployed by Pyntara $version
After=local-fs.target

[Service]
Type=oneshot
Restart=on-failure
$exec_lines
"""

COLLECTOR_TIMER_TEMPLATE = """\
[Unit]
Description=System Metrics report collector timer
# Deployed by Pyntara $version

[Timer]
OnBootSec=$boot_delay_seconds
$daily_send_calendar
Unit=$service_unit_name

[Install]
WantedBy=timers.target
"""

COMMAND_TEMPLATE = """\
#!/usr/bin/env bash
SPOOL_DIR='@SPOOL_DIR@'
JOURNAL_IDENTIFIER='@JOURNAL_IDENTIFIER@'
TEMP_PREFIX='@TEMP_PREFIX@'
"""

SERVICE_JOURNAL_IDENTIFIER = "system_metrics"
COMMIT_JOURNAL_IDENTIFIER = "commit_system_metrics"
SPOOL_TEMP_PREFIX = ".commit-"
COLLECTOR_JOURNAL_IDENTIFIER = "system_metrics_collector"
COLLECTOR_SERVICE_NAME = "system_metrics_collector.service"
COLLECTOR_TIMER_NAME = "system_metrics_collector.timer"


class SystemMetricsFixtures(TypedDict):
    """Temporary deployment paths."""

    repo: Path
    venv_dir: Path
    venv_python: Path
    command_path: Path
    spool_dir: Path
    systemd_dir: Path


def _ctx(tmp_path: Path, *, force: bool = False) -> Context:
    """Context with the safe defaults the engine fills in a real run."""

    return make_context(
        task_name="system_metrics_setup",
        install_mode="server",
        force_tasks=frozenset({"system_metrics_setup"}) if force else frozenset(),
        repo_root=tmp_path / "repo",
        task_data_root=tmp_path,
        skip_apt_update=True,
    )


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    venv_ok: bool = False,
) -> SystemMetricsFixtures:
    """Point the task at temporary fixtures; return the fixture paths.

    The repository clone is a temporary directory holding the unit and
    command templates; the venv, the spool and the unit directory are
    temporary paths as well, so the real machine is never touched.
    """

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    task_data = repo / "task_data" / "system_metrics_setup"
    task_data.mkdir(parents=True)
    service_template = task_data / "system_metrics.service"
    service_template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    ingest_service_template = task_data / "system_metrics-ingest.service"
    ingest_service_template.write_text(INGEST_SERVICE_TEMPLATE, encoding="utf-8")
    ingest_path_template = task_data / "system_metrics-ingest.path"
    ingest_path_template.write_text(INGEST_PATH_TEMPLATE, encoding="utf-8")
    collector_service_template = task_data / "system_metrics_collector.service"
    collector_service_template.write_text(COLLECTOR_SERVICE_TEMPLATE, encoding="utf-8")
    collector_timer_template = task_data / "system_metrics_collector.timer"
    collector_timer_template.write_text(COLLECTOR_TIMER_TEMPLATE, encoding="utf-8")
    command_template = task_data / "commit_system_metrics.sh"
    command_template.write_text(COMMAND_TEMPLATE, encoding="utf-8")
    venv_dir = tmp_path / "usr" / "local" / "lib" / "pyntara" / "venv"
    venv_python = venv_dir / "bin" / "python"
    if venv_ok:
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    command_path = tmp_path / "usr" / "local" / "bin" / "commit_system_metrics"
    spool_dir = tmp_path / "var" / "spool" / "system_metrics"
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(engine_values, "SYSTEMD_UNIT_DIR", systemd_dir)
    monkeypatch.setattr(values, "VENV_DIR", venv_dir)
    monkeypatch.setattr(values, "COMMAND_PATH", command_path)
    monkeypatch.setattr(values, "SPOOL_DIR", spool_dir)
    return {
        "repo": repo,
        "venv_dir": venv_dir,
        "venv_python": venv_python,
        "command_path": command_path,
        "spool_dir": spool_dir,
        "systemd_dir": systemd_dir,
    }


def _expected_service_unit(
    fixtures: SystemMetricsFixtures, version: str = __version__
) -> str:
    """The service unit the task must render for the given fixtures."""

    command = " ".join(
        [
            str(fixtures["venv_python"]),
            "-m",
            "pyntara.metrics",
        ]
    )
    return Template(UNIT_TEMPLATE).substitute(
        exec_lines=f"ExecStart={command}",
        version=version,
    )


def _expected_ingest_service_unit(
    fixtures: SystemMetricsFixtures, version: str = __version__
) -> str:
    """The ingest service unit the task must render for the fixtures."""

    command = " ".join(
        [
            str(fixtures["venv_python"]),
            "-m",
            "pyntara.metrics_ingest",
        ]
    )
    return Template(INGEST_SERVICE_TEMPLATE).substitute(
        exec_lines=f"ExecStart={command}",
        version=version,
    )


def _expected_ingest_path_unit(
    fixtures: SystemMetricsFixtures, version: str = __version__
) -> str:
    """The path unit the task must render for the given fixtures."""

    return Template(INGEST_PATH_TEMPLATE).substitute(
        spool_dir=fixtures["spool_dir"], version=version
    )


def _expected_collector_service_unit(
    fixtures: SystemMetricsFixtures, version: str = __version__
) -> str:
    """The collector service unit the task must render for the fixtures."""

    command = " ".join(
        [
            str(fixtures["venv_python"]),
            "-m",
            "pyntara.metrics_collect",
        ]
    )
    return Template(COLLECTOR_SERVICE_TEMPLATE).substitute(
        exec_lines=f"ExecStart={command}",
        version=version,
    )


def _expected_collector_timer_unit(
    fixtures: SystemMetricsFixtures, version: str = __version__
) -> str:
    """The collector timer unit the task must render for the fixtures."""

    collector = values.COLLECTOR
    calendar = "\n".join(
        f"OnCalendar=*-*-* {time_of_day}" for time_of_day in collector.daily_send_times
    )
    return Template(COLLECTOR_TIMER_TEMPLATE).substitute(
        boot_delay_seconds=collector.boot_delay_seconds,
        daily_send_calendar=calendar,
        service_unit_name=collector.service_unit_name,
        version=version,
    )


def _expected_command(fixtures: SystemMetricsFixtures) -> str:
    """The commit command the task must render for the given fixtures."""

    return (
        COMMAND_TEMPLATE.replace("@SPOOL_DIR@", str(fixtures["spool_dir"]))
        .replace("@JOURNAL_IDENTIFIER@", COMMIT_JOURNAL_IDENTIFIER)
        .replace("@TEMP_PREFIX@", SPOOL_TEMP_PREFIX)
    )


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled_names: set[str],
    active_names: set[str],
    import_ok: bool,
    uv_available: bool = True,
    venv_version: str = __version__,
    fail: Callable[[list[str]], bool] | None = None,
) -> list[list[str]]:
    """Install subprocess and uv fakes; return the recorded command calls.

    systemctl is-enabled and is-active answer from the given name sets,
    the venv python import answers from import_ok with the venv_version
    as the printed pyntara version, and uv commands succeed unless
    matched by fail.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if fail is not None and fail(command):
            raise subprocess.CalledProcessError(1, command)
        if command[0] == "systemctl" and command[1] == "is-enabled":
            if command[2] in enabled_names:
                return _FakeProc(0, "enabled\n")
            return _FakeProc(1, "disabled")
        if command[0] == "systemctl" and command[1] == "is-active":
            if command[2] in active_names:
                return _FakeProc(0, "active\n")
            return _FakeProc(1, "inactive")
        if command[0].endswith("/python") and command[1] == "-c":
            if import_ok:
                return _FakeProc(0, f"{venv_version}\n")
            return _FakeProc(1)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    monkeypatch.setattr(
        system_metrics_setup,
        "_uv_path",
        lambda: "uv" if uv_available else None,
    )
    return calls


def _deploy_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    service_enabled: bool = False,
    path_enabled: bool = False,
    timer_enabled: bool = False,
    service_active: bool = False,
    path_active: bool = False,
    timer_active: bool = False,
    import_ok: bool = False,
    deployed: bool = False,
    venv_version: str = __version__,
    command_ok: bool = True,
    spool_ok: bool = True,
    stale_path_unit: bool = False,
    uv_available: bool = True,
    fail: Callable[[list[str]], bool] | None = None,
) -> tuple[SystemMetricsFixtures, list[list[str]]]:
    """Fixtures plus a fake; when deployed, all state matches the sources.

    stale_path_unit
    leaves the path unit unwritten, so tests can exercise exactly one
    drift at a time.
    """

    fixtures = _install_fixtures(
        monkeypatch,
        tmp_path,
        venv_ok=deployed or import_ok,
    )
    if deployed:
        fixtures["systemd_dir"].mkdir(parents=True)
        for name, expected in (
            ("system_metrics.service", _expected_service_unit(fixtures)),
            (
                "system_metrics-ingest.service",
                _expected_ingest_service_unit(fixtures),
            ),
            ("system_metrics-ingest.path", _expected_ingest_path_unit(fixtures)),
            (
                "system_metrics_collector.service",
                _expected_collector_service_unit(fixtures),
            ),
            (
                "system_metrics_collector.timer",
                _expected_collector_timer_unit(fixtures),
            ),
        ):
            if stale_path_unit and name == "system_metrics-ingest.path":
                continue
            (fixtures["systemd_dir"] / name).write_text(expected, encoding="utf-8")
        if command_ok:
            fixtures["command_path"].parent.mkdir(parents=True)
            fixtures["command_path"].write_text(
                _expected_command(fixtures), encoding="utf-8"
            )
            os.chmod(fixtures["command_path"], 0o755)
        if spool_ok:
            fixtures["spool_dir"].mkdir(parents=True)
            os.chmod(fixtures["spool_dir"], 0o1733)
    service_name = values.SERVICE_UNIT_NAME
    path_name = values.INGEST_PATH_UNIT_NAME
    timer_name = values.COLLECTOR.timer_unit_name
    enabled_names: set[str] = set()
    if service_enabled:
        enabled_names.add(service_name)
    if path_enabled:
        enabled_names.add(path_name)
    if timer_enabled:
        enabled_names.add(timer_name)
    active_names: set[str] = set()
    if service_active:
        active_names.add(service_name)
    if path_active:
        active_names.add(path_name)
    if timer_active:
        active_names.add(timer_name)
    calls = _install_fake(
        monkeypatch,
        enabled_names=enabled_names,
        active_names=active_names,
        import_ok=import_ok,
        uv_available=uv_available,
        venv_version=venv_version,
        fail=fail,
    )
    return fixtures, calls


def test_unit_template_name_comes_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The name of the template the task reads is a config value: another
    # name in the table is the file the task reads while the shipped name is
    # absent, so a name spelled in the code would fail here, and the target
    # machine would fail with it.
    fixtures, _calls = _deploy_fixture(monkeypatch, tmp_path)
    task_data = fixtures["repo"] / "task_data" / "system_metrics_setup"
    (task_data / "system_metrics.service").rename(task_data / "renamed.service")
    monkeypatch.setattr(values, "UNIT_TEMPLATE_FILE_NAME", "renamed.service")
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    deployed = fixtures["systemd_dir"] / "system_metrics.service"
    assert deployed.read_text(encoding="utf-8") == _expected_service_unit(fixtures)


def test_deploys_service_ingest_and_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Nothing is deployed: the task creates the venv, installs the package
    # from the clone, copies the config, writes the five units, enables
    # and starts the service, the path unit and the collector timer,
    # writes the commit command and creates the spool directory.
    fixtures, calls = _deploy_fixture(monkeypatch, tmp_path)
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    settings = values
    venv_create = list(
        substituted_command(
            settings.VENV_CREATE_COMMAND,
            {
                "uv": "uv",
                "venv_dir": str(fixtures["venv_dir"]),
                "python_version": settings.PYTHON_VERSION,
            },
        )
    )
    assert venv_create in calls
    assert any(
        call[0] == "uv"
        and call[1] == "sync"
        and "--project" in call
        and "--active" in call
        and "--locked" in call
        and "--no-dev" in call
        and "--no-editable" in call
        and "--reinstall-package" not in call
        for call in calls
    )
    assert (fixtures["systemd_dir"] / "system_metrics.service").read_text(
        encoding="utf-8"
    ) == _expected_service_unit(fixtures)
    assert (fixtures["systemd_dir"] / "system_metrics-ingest.service").read_text(
        encoding="utf-8"
    ) == _expected_ingest_service_unit(fixtures)
    assert (fixtures["systemd_dir"] / "system_metrics-ingest.path").read_text(
        encoding="utf-8"
    ) == _expected_ingest_path_unit(fixtures)
    assert (fixtures["systemd_dir"] / "system_metrics_collector.service").read_text(
        encoding="utf-8"
    ) == _expected_collector_service_unit(fixtures)
    assert (fixtures["systemd_dir"] / "system_metrics_collector.timer").read_text(
        encoding="utf-8"
    ) == _expected_collector_timer_unit(fixtures)
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "system_metrics.service"] in calls
    assert ["systemctl", "enable", "system_metrics-ingest.path"] in calls
    assert ["systemctl", "enable", "system_metrics_collector.timer"] in calls
    assert ["systemctl", "start", "system_metrics.service"] in calls
    assert ["systemctl", "start", "system_metrics-ingest.path"] in calls
    assert ["systemctl", "start", "system_metrics_collector.timer"] in calls
    assert fixtures["command_path"].read_text(encoding="utf-8") == _expected_command(
        fixtures
    )
    assert os.stat(fixtures["command_path"]).st_mode & 0o777 == 0o755
    assert os.stat(fixtures["spool_dir"]).st_mode & 0o7777 == 0o1733
    assert "System Metrics service deployed" in (result.message or "")
    captured = capsys.readouterr()
    assert "creating venv" in captured.out


def test_the_units_carry_the_version_of_the_deployed_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The version in every unit is the one the deployed interpreter
    # reports, so a unit on the machine that names another version is
    # stale and the task writes it again.
    fixtures, _calls = _deploy_fixture(
        monkeypatch, tmp_path, import_ok=True, venv_version="0.3.999"
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success
    for unit_name in (
        "system_metrics.service",
        "system_metrics-ingest.service",
        "system_metrics-ingest.path",
        "system_metrics_collector.service",
        "system_metrics_collector.timer",
    ):
        unit = (fixtures["systemd_dir"] / unit_name).read_text(encoding="utf-8")
        assert "# Deployed by Pyntara 0.3.999" in unit


def test_a_deployment_that_cannot_be_asked_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A venv that cannot be asked leaves the repository version in the
    # units and names the gap, so the missing refresh is visible in the
    # install log instead of being passed off as the new code.
    fixtures, _calls = _deploy_fixture(monkeypatch, tmp_path)
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success
    unit = (fixtures["systemd_dir"] / "system_metrics.service").read_text(
        encoding="utf-8"
    )
    assert f"# Deployed by Pyntara {__version__}" in unit
    assert any("cannot read the version" in warning for warning in result.warnings)


def test_skips_when_already_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv imports pyntara, the config, the units and the command
    # match and the service and the path unit are enabled: only status
    # queries run, nothing changes.
    _fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert not any(call[0] == "uv" for call in calls)
    assert not any(
        call[0] == "systemctl" and call[1] in ("start", "restart", "enable")
        for call in calls
    )


def test_force_reinstalls_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is already configured but the task is forced: the package
    # is reinstalled with --reinstall-package pyntara, the config and units
    # rewritten, the service and the path unit enabled and restarted.
    _fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(
        _ctx(tmp_path, force=True)
    )
    assert result.success is True
    assert result.changed is True
    assert any(
        call[0] == "uv"
        and call[1] == "sync"
        and "--reinstall-package" in call
        and "pyntara" in call
        for call in calls
    )
    assert ["systemctl", "enable", "system_metrics.service"] in calls
    assert ["systemctl", "enable", "system_metrics-ingest.path"] in calls
    assert ["systemctl", "restart", "system_metrics.service"] in calls
    assert ["systemctl", "restart", "system_metrics-ingest.path"] in calls
    assert not any(
        call == ["systemctl", "start", "system_metrics.service"] for call in calls
    )


def test_stale_venv_is_updated_and_service_restarted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv runs an old pyntara version while everything else is
    # deployed: the task reinstalls the package with --reinstall-package
    # pyntara and restarts the long-running service, so the new code takes
    # effect without a reboot.
    _fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        timer_enabled=True,
        service_active=True,
        path_active=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
        venv_version="0.0.1",
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert any(
        call[0] == "uv"
        and call[1] == "sync"
        and "--reinstall-package" in call
        and "pyntara" in call
        for call in calls
    )
    assert ["systemctl", "restart", "system_metrics.service"] in calls


def test_uv_missing_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without uv on PATH no virtual environment can be built: the step is
    # reported and the configuration, the units, the command and the
    # spool directory are still deployed.
    _fixtures, _ = _deploy_fixture(monkeypatch, tmp_path, uv_available=False)
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert any("uv" in warning for warning in result.warnings)


def test_uv_sync_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed package install is reported as a warning: the task
    # completes and every later step still runs.
    def fail_uv_sync(command: list[str]) -> bool:
        return command[0] == "uv" and command[1] == "sync"

    _fixtures, calls = _deploy_fixture(monkeypatch, tmp_path, fail=fail_uv_sync)
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert any("cannot install" in w for w in result.warnings)
    assert any(call[:2] == ["systemctl", "enable"] for call in calls)


def test_only_service_disabled_starts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv, config, units, command and spool are in place, only the
    # boot service is missing: no venv or config work happens, the service
    # is enabled and started, the path unit is untouched.
    _fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=False,
        path_enabled=True,
        service_active=False,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "uv" for call in calls)
    assert ["systemctl", "enable", "system_metrics.service"] in calls
    assert ["systemctl", "start", "system_metrics.service"] in calls
    assert not any(
        call == ["systemctl", "start", "system_metrics-ingest.path"] for call in calls
    )


def test_only_command_missing_writes_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv, config, units, service enablement and spool are in place,
    # only the command file is missing: no venv or systemd work happens,
    # the command is written with the configured mode.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
        command_ok=False,
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "uv" for call in calls)
    assert not any(
        call[0] == "systemctl"
        and call[1] in ("daemon-reload", "enable", "start", "restart")
        for call in calls
    )
    assert fixtures["command_path"].read_text(encoding="utf-8") == _expected_command(
        fixtures
    )
    assert os.stat(fixtures["command_path"]).st_mode & 0o777 == 0o755


def test_command_stale_content_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A stale command file (wrong content or mode) is rewritten: the path
    # is explicitly configured, so a foreign file is a conflict the
    # operator wants resolved, not a reason to abort.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
        command_ok=False,
    )
    fixtures["command_path"].parent.mkdir(parents=True)
    fixtures["command_path"].write_text("stale\n", encoding="utf-8")
    os.chmod(fixtures["command_path"], 0o644)
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert fixtures["command_path"].read_text(encoding="utf-8") == _expected_command(
        fixtures
    )
    assert os.stat(fixtures["command_path"]).st_mode & 0o777 == 0o755


def test_command_directory_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory on the command path cannot be replaced (no recursive
    # removal): the task reports the reason and leaves the directory
    # alone.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
        command_ok=False,
    )
    fixtures["command_path"].mkdir(parents=True)
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert any("directory" in warning for warning in result.warnings)
    assert fixtures["command_path"].is_dir()


def test_only_spool_missing_creates_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is in place except the spool directory: no venv or
    # systemd work happens, the spool is created with the configured mode.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
        spool_ok=False,
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "uv" for call in calls)
    assert not any(
        call[0] == "systemctl"
        and call[1] in ("daemon-reload", "enable", "start", "restart")
        for call in calls
    )
    assert os.stat(fixtures["spool_dir"]).st_mode & 0o7777 == 0o1733


def test_spool_wrong_mode_fixed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The spool exists with the wrong mode: the mode is corrected to the
    # configured 1733.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
        spool_ok=False,
    )
    fixtures["spool_dir"].mkdir(parents=True)
    os.chmod(fixtures["spool_dir"], 0o755)
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert os.stat(fixtures["spool_dir"]).st_mode & 0o7777 == 0o1733


def test_permission_masks_come_from_the_config(tmp_path: Path) -> None:
    # The mode checks compare through the configured masks: a mask that
    # keeps the permission bits only accepts the command file, and a mask
    # that includes the special bits makes the same comparison fail while
    # the spool check needs the wider mask to see the sticky bit of 1733.
    command = tmp_path / "commit_system_metrics"
    command.write_text("body", encoding="utf-8")
    command.chmod(0o755)
    assert system_metrics_setup._command_file_matches(command, "body", 0o755, 0o777)
    assert not system_metrics_setup._command_file_matches(
        command, "body", 0o4755, 0o7777
    )
    spool = tmp_path / "spool"
    spool.mkdir()
    spool.chmod(0o1733)
    assert system_metrics_setup._spool_dir_ok(spool, 0o1733, 0o7777)
    assert not system_metrics_setup._spool_dir_ok(spool, 0o1733, 0o777)


def test_service_exec_line_comes_from_the_declared_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The line a deployed unit starts with is a declared value: another
    # command is exactly what the unit runs, with the venv interpreter
    # filling its placeholder.
    monkeypatch.setattr(values, "SEND_SERVICE_COMMAND", ("myrun", "-m", "mymod"))
    template = tmp_path / "system_metrics.service"
    template.write_text(
        "[Service]\n$exec_lines\nRestart=on-failure\n",
        encoding="utf-8",
    )
    unit = system_metrics_setup._render_service_unit(
        template,
        Path("/venv/bin/python"),
        "0.3.516",
    )
    assert "ExecStart=myrun -m mymod" in unit
    assert "Restart=on-failure" in unit


def test_collector_calendar_comes_from_the_config(tmp_path: Path) -> None:
    # The proof of the value: every time of day of the collector table is
    # one OnCalendar line of the deployed timer, so the operator decides
    # when the report is built without a code change.
    template = tmp_path / "system_metrics_collector.timer"
    template.write_text(
        "[Timer]\n$daily_send_calendar\nUnit=collector.service\n",
        encoding="utf-8",
    )
    unit = system_metrics_setup._render_collector_timer_unit(
        template,
        boot_delay_seconds=30,
        daily_send_times=("06:30:00", "18:15:00"),
        service_unit_name="collector.service",
        version="0.3.516",
    )
    assert "OnCalendar=*-*-* 06:30:00" in unit
    assert "OnCalendar=*-*-* 18:15:00" in unit
    assert "Unit=collector.service" in unit


def test_only_path_unit_disabled_enables_and_starts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is in place except the path unit enablement: no venv work
    # happens, the path unit is enabled and started.
    _fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=False,
        service_active=True,
        path_active=False,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "uv" for call in calls)
    assert ["systemctl", "enable", "system_metrics-ingest.path"] in calls
    assert ["systemctl", "start", "system_metrics-ingest.path"] in calls


def test_path_unit_stale_restarted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The path unit file is stale (the spool path changed) and the unit is
    # running: it must be restarted to watch the new directory.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
        stale_path_unit=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert (fixtures["systemd_dir"] / "system_metrics-ingest.path").read_text(
        encoding="utf-8"
    ) == _expected_ingest_path_unit(fixtures)
    assert ["systemctl", "restart", "system_metrics-ingest.path"] in calls


def test_force_recreates_command_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # In force mode the command file is rewritten even when it already
    # matches.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        timer_enabled=True,
        timer_active=True,
        import_ok=True,
        deployed=True,
    )
    # A hard link keeps the old inode alive, because a filesystem is free to
    # hand the number of a just-deleted file to the new one, which the ext4
    # temporary directory of a continuous integration runner does. Without
    # the link the comparison below is a coin toss; with it the number of the
    # old file cannot be reused, so a file left in place is still caught.
    alias = tmp_path / "commit_system_metrics.before"
    os.link(fixtures["command_path"], alias)
    inode_before = alias.stat().st_ino
    result = system_metrics_setup.task(
        _ctx(tmp_path, force=True)
    )
    assert result.success is True
    assert result.changed is True
    assert fixtures["command_path"].read_text(encoding="utf-8") == _expected_command(
        fixtures
    )
    assert fixtures["command_path"].stat().st_ino != inode_before


def test_systemctl_commands_come_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The reload of systemd and the enable, restart and start of a unit are
    # config values: another command line in the section is exactly the argv
    # the task runs.
    _fixtures, calls = _deploy_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        values,
        "SYSTEMCTL_DAEMON_RELOAD_COMMAND",
        ("systemctl", "daemon-reload", "--quiet"),
    )
    monkeypatch.setattr(
        values,
        "SYSTEMCTL_ENABLE_COMMAND",
        ("systemctl", "enable", "{unit_name}", "--quiet"),
    )
    monkeypatch.setattr(
        values,
        "SYSTEMCTL_RESTART_COMMAND",
        ("systemctl", "restart", "{unit_name}", "--no-block"),
    )
    monkeypatch.setattr(
        values,
        "SYSTEMCTL_START_COMMAND",
        ("systemctl", "start", "{unit_name}", "--no-block"),
    )
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is True
    assert ["systemctl", "daemon-reload", "--quiet"] in calls
    assert [
        "systemctl",
        "start",
        values.SERVICE_UNIT_NAME,
        "--no-block",
    ] in calls
    result = system_metrics_setup.task(_ctx(tmp_path, force=True))
    assert result.success is True
    assert [
        "systemctl",
        "enable",
        values.SERVICE_UNIT_NAME,
        "--quiet",
    ] in calls
    assert [
        "systemctl",
        "restart",
        values.SERVICE_UNIT_NAME,
        "--no-block",
    ] in calls
