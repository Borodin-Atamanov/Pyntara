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

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.config import Config
from pyntara.context import Context
from pyntara.tasks import system_metrics_setup

UNIT_TEMPLATE = """\
[Unit]
Description=System Metrics service
After=local-fs.target

[Service]
Type=simple
Environment=PYNTARA_JOURNAL_IDENTIFIER=$journal_identifier
StandardOutput=null
Restart=on-failure
$exec_lines

[Install]
WantedBy=multi-user.target
"""

INGEST_SERVICE_TEMPLATE = """\
[Unit]
Description=System Metrics spool ingest
After=local-fs.target

[Service]
Type=oneshot
Environment=PYNTARA_JOURNAL_IDENTIFIER=$journal_identifier
$exec_lines
"""

INGEST_PATH_TEMPLATE = """\
[Unit]
Description=Watch the System Metrics spool directory
After=local-fs.target

[Path]
PathChanged=$spool_dir

[Install]
WantedBy=multi-user.target
"""

COMMAND_TEMPLATE = """\
#!/usr/bin/env bash
SPOOL_DIR='$spool_dir'
JOURNAL_IDENTIFIER='$commit_journal_identifier'
TEMP_PREFIX='$spool_temp_prefix'
"""

SERVICE_JOURNAL_IDENTIFIER = "system_metrics"
COMMIT_JOURNAL_IDENTIFIER = "commit_system_metrics"
SPOOL_TEMP_PREFIX = ".commit-"


def _ctx(
    tmp_path: Path, *, force: bool = False, config: Config | None = None
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        install_mode="server",
        force_tasks=frozenset({"system_metrics_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=config if config is not None else make_config(task_data_root=tmp_path),
    )


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    venv_ok: bool = False,
) -> dict[str, Path]:
    """Point the task at temporary fixtures; return the fixture paths.

    The repository clone is a temporary directory holding config.toml and
    the unit and command templates; the venv, the system config, the
    spool and the unit directory are temporary paths as well, so the real
    machine is never touched.
    """

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    source_config = repo / "config.toml"
    source_config.write_text(
        "[system_metrics_setup]\ncheck_interval_seconds = 300\n", encoding="utf-8"
    )
    task_data = repo / "task_data" / "system_metrics_setup"
    task_data.mkdir(parents=True)
    service_template = task_data / "system_metrics.service"
    service_template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    ingest_service_template = task_data / "system_metrics-ingest.service"
    ingest_service_template.write_text(INGEST_SERVICE_TEMPLATE, encoding="utf-8")
    ingest_path_template = task_data / "system_metrics-ingest.path"
    ingest_path_template.write_text(INGEST_PATH_TEMPLATE, encoding="utf-8")
    command_template = task_data / "commit_system_metrics.sh"
    command_template.write_text(COMMAND_TEMPLATE, encoding="utf-8")
    venv_dir = tmp_path / "usr" / "local" / "lib" / "pyntara" / "venv"
    venv_python = venv_dir / "bin" / "python"
    if venv_ok:
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    command_path = tmp_path / "usr" / "local" / "bin" / "commit_system_metrics"
    spool_dir = tmp_path / "var" / "spool" / "system_metrics"
    system_config_dir = tmp_path / "etc" / "pyntara"
    system_config = system_config_dir / "config.toml"
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(system_metrics_setup, "REPO_ROOT", repo)
    monkeypatch.setattr(system_metrics_setup, "TEMPLATE_PATH", service_template)
    monkeypatch.setattr(
        system_metrics_setup, "INGEST_SERVICE_TEMPLATE_PATH", ingest_service_template
    )
    monkeypatch.setattr(
        system_metrics_setup, "INGEST_PATH_TEMPLATE_PATH", ingest_path_template
    )
    monkeypatch.setattr(system_metrics_setup, "COMMAND_TEMPLATE_PATH", command_template)
    monkeypatch.setattr(system_metrics_setup, "SYSTEMD_UNIT_DIR", systemd_dir)
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_venv_dir=venv_dir,
        system_metrics_system_config_path=system_config,
        system_metrics_command_path=command_path,
        system_metrics_spool_dir=spool_dir,
    )
    return {
        "repo": repo,
        "source_config": source_config,
        "venv_dir": venv_dir,
        "venv_python": venv_python,
        "command_path": command_path,
        "spool_dir": spool_dir,
        "system_config": system_config,
        "systemd_dir": systemd_dir,
        "config": config,
    }


def _expected_service_unit(fixtures: dict[str, Path]) -> str:
    """The service unit the task must render for the given fixtures."""

    command = " ".join(
        [
            str(fixtures["venv_python"]),
            "-m",
            "pyntara.metrics",
            str(fixtures["system_config"]),
        ]
    )
    return Template(UNIT_TEMPLATE).substitute(
        exec_lines=f"ExecStart={command}",
        journal_identifier=SERVICE_JOURNAL_IDENTIFIER,
    )


def _expected_ingest_service_unit(fixtures: dict[str, Path]) -> str:
    """The ingest service unit the task must render for the fixtures."""

    command = " ".join(
        [
            str(fixtures["venv_python"]),
            "-m",
            "pyntara.metrics_ingest",
            str(fixtures["system_config"]),
        ]
    )
    return Template(INGEST_SERVICE_TEMPLATE).substitute(
        exec_lines=f"ExecStart={command}",
        journal_identifier=SERVICE_JOURNAL_IDENTIFIER,
    )


def _expected_ingest_path_unit(fixtures: dict[str, Path]) -> str:
    """The path unit the task must render for the given fixtures."""

    return Template(INGEST_PATH_TEMPLATE).substitute(spool_dir=fixtures["spool_dir"])


def _expected_command(fixtures: dict[str, Path]) -> str:
    """The commit command the task must render for the given fixtures."""

    return Template(COMMAND_TEMPLATE).substitute(
        spool_dir=fixtures["spool_dir"],
        commit_journal_identifier=COMMIT_JOURNAL_IDENTIFIER,
        spool_temp_prefix=SPOOL_TEMP_PREFIX,
    )


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled_names: set[str],
    active_names: set[str],
    import_ok: bool,
    uv_available: bool = True,
    fail: Callable[[list[str]], bool] | None = None,
) -> list[list[str]]:
    """Install subprocess and uv fakes; return the recorded command calls.

    systemctl is-enabled and is-active answer from the given name sets,
    the venv python import answers from import_ok, and uv commands
    succeed unless matched by fail.
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
                return _FakeProc(0)
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
    service_active: bool = False,
    path_active: bool = False,
    import_ok: bool = False,
    deployed: bool = False,
    command_ok: bool = True,
    spool_ok: bool = True,
    stale_config: bool = False,
    stale_path_unit: bool = False,
    uv_available: bool = True,
    fail: Callable[[list[str]], bool] | None = None,
) -> tuple[dict[str, Path], list[list[str]]]:
    """Fixtures plus a fake; when deployed, all state matches the sources.

    stale_config leaves the system config unwritten and stale_path_unit
    leaves the path unit unwritten, so tests can exercise exactly one
    drift at a time.
    """

    fixtures = _install_fixtures(
        monkeypatch,
        tmp_path,
        venv_ok=deployed or import_ok,
    )
    if deployed:
        if not stale_config:
            fixtures["system_config"].parent.mkdir(parents=True)
            fixtures["system_config"].write_text(
                fixtures["source_config"].read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        fixtures["systemd_dir"].mkdir(parents=True)
        for name, expected in (
            ("system_metrics.service", _expected_service_unit(fixtures)),
            (
                "system_metrics-ingest.service",
                _expected_ingest_service_unit(fixtures),
            ),
            ("system_metrics-ingest.path", _expected_ingest_path_unit(fixtures)),
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
    service_name = fixtures["config"].system_metrics_setup.service_unit_name
    path_name = fixtures["config"].system_metrics_setup.ingest_path_unit_name
    enabled_names: set[str] = set()
    if service_enabled:
        enabled_names.add(service_name)
    if path_enabled:
        enabled_names.add(path_name)
    active_names: set[str] = set()
    if service_active:
        active_names.add(service_name)
    if path_active:
        active_names.add(path_name)
    calls = _install_fake(
        monkeypatch,
        enabled_names=enabled_names,
        active_names=active_names,
        import_ok=import_ok,
        uv_available=uv_available,
        fail=fail,
    )
    return fixtures, calls


def test_deploys_service_ingest_and_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Nothing is deployed: the task creates the venv, installs the package
    # from the clone, copies the config, writes the three units, enables
    # and starts the service and the path unit, writes the commit command
    # and creates the spool directory.
    fixtures, calls = _deploy_fixture(monkeypatch, tmp_path)
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert ["uv", "venv", str(fixtures["venv_dir"]), "--python", "3"] in calls
    assert any(
        call[0] == "uv" and call[1] == "pip" and call[2] == "install"
        and "--python" in call
        for call in calls
    )
    assert fixtures["system_config"].read_text(encoding="utf-8") == (
        fixtures["source_config"].read_text(encoding="utf-8")
    )
    assert (
        fixtures["systemd_dir"] / "system_metrics.service"
    ).read_text(encoding="utf-8") == _expected_service_unit(fixtures)
    assert (
        fixtures["systemd_dir"] / "system_metrics-ingest.service"
    ).read_text(encoding="utf-8") == _expected_ingest_service_unit(fixtures)
    assert (
        fixtures["systemd_dir"] / "system_metrics-ingest.path"
    ).read_text(encoding="utf-8") == _expected_ingest_path_unit(fixtures)
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "system_metrics.service"] in calls
    assert ["systemctl", "enable", "system_metrics-ingest.path"] in calls
    assert ["systemctl", "start", "system_metrics.service"] in calls
    assert ["systemctl", "start", "system_metrics-ingest.path"] in calls
    assert fixtures["command_path"].read_text(encoding="utf-8") == _expected_command(
        fixtures
    )
    assert os.stat(fixtures["command_path"]).st_mode & 0o777 == 0o755
    assert os.stat(fixtures["spool_dir"]).st_mode & 0o7777 == 0o1733
    assert "System Metrics service deployed" in (result.message or "")
    captured = capsys.readouterr()
    assert "creating venv" in captured.out


def test_skips_when_already_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv imports pyntara, the config, the units and the command
    # match and the service and the path unit are enabled: only status
    # queries run, nothing changes.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert not any(call[0] == "uv" for call in calls)
    assert not any(call[0] == "systemctl" and call[1] in ("start", "restart", "enable") for call in calls)
    assert fixtures["system_config"].read_text(encoding="utf-8") == (
        fixtures["source_config"].read_text(encoding="utf-8")
    )


def test_force_reinstalls_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is already configured but the task is forced: the package
    # is reinstalled with --reinstall, the config and units rewritten, the
    # service and the path unit enabled and restarted.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, force=True, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert any(
        call[0] == "uv" and call[1] == "pip" and call[2] == "install"
        and "--reinstall" in call
        for call in calls
    )
    assert ["systemctl", "enable", "system_metrics.service"] in calls
    assert ["systemctl", "enable", "system_metrics-ingest.path"] in calls
    assert ["systemctl", "restart", "system_metrics.service"] in calls
    assert ["systemctl", "restart", "system_metrics-ingest.path"] in calls
    assert not any(call == ["systemctl", "start", "system_metrics.service"] for call in calls)


def test_uv_missing_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without uv on PATH there is no way to create the venv: the task
    # fails loudly instead of leaving a half-deployed service.
    _deploy_fixture(monkeypatch, tmp_path, uv_available=False)
    result = system_metrics_setup.task(_ctx(tmp_path))
    assert result.success is False
    assert "uv" in (result.error or "")


def test_uv_pip_install_failure_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed package install is an error: the task reports it and the
    # runner continues with the remaining tasks.
    def fail_uv_install(command: list[str]) -> bool:
        return command[0] == "uv" and command[1] == "pip" and command[2] == "install"

    fixtures, _ = _deploy_fixture(monkeypatch, tmp_path, fail=fail_uv_install)
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is False
    assert "cannot install" in (result.error or "")


def test_only_service_disabled_starts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv, config, units, command and spool are in place, only the
    # boot service is missing: no venv or config work happens, the service
    # is enabled and started, the path unit is untouched.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=False,
        path_enabled=True,
        service_active=False,
        path_active=True,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "uv" for call in calls)
    assert ["systemctl", "enable", "system_metrics.service"] in calls
    assert ["systemctl", "start", "system_metrics.service"] in calls
    assert not any(call == ["systemctl", "start", "system_metrics-ingest.path"] for call in calls)
    assert fixtures["system_config"].read_text(encoding="utf-8") == (
        fixtures["source_config"].read_text(encoding="utf-8")
    )


def test_config_change_restarts_running_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv, units, command and spool are fine but the system config is
    # stale and the service is already running: it must be restarted to
    # pick up the new config, not started again.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        import_ok=True,
        deployed=True,
        stale_config=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "system_metrics.service"] in calls
    assert not any(call == ["systemctl", "start", "system_metrics.service"] for call in calls)
    assert not any(call == ["systemctl", "restart", "system_metrics-ingest.path"] for call in calls)
    assert fixtures["system_config"].read_text(encoding="utf-8") == (
        fixtures["source_config"].read_text(encoding="utf-8")
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
        import_ok=True,
        deployed=True,
        command_ok=False,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
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
        import_ok=True,
        deployed=True,
        command_ok=False,
    )
    fixtures["command_path"].parent.mkdir(parents=True)
    fixtures["command_path"].write_text("stale\n", encoding="utf-8")
    os.chmod(fixtures["command_path"], 0o644)
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert fixtures["command_path"].read_text(encoding="utf-8") == _expected_command(
        fixtures
    )
    assert os.stat(fixtures["command_path"]).st_mode & 0o777 == 0o755


def test_command_directory_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory on the command path cannot be replaced (no recursive
    # removal): the task fails with a clear error and leaves the
    # directory alone.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=True,
        service_active=True,
        path_active=True,
        import_ok=True,
        deployed=True,
        command_ok=False,
    )
    fixtures["command_path"].mkdir(parents=True)
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is False
    assert "directory" in (result.error or "")
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
        import_ok=True,
        deployed=True,
        spool_ok=False,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
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
        import_ok=True,
        deployed=True,
        spool_ok=False,
    )
    fixtures["spool_dir"].mkdir(parents=True)
    os.chmod(fixtures["spool_dir"], 0o755)
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert os.stat(fixtures["spool_dir"]).st_mode & 0o7777 == 0o1733


def test_only_path_unit_disabled_enables_and_starts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Everything is in place except the path unit enablement: no venv work
    # happens, the path unit is enabled and started.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        service_enabled=True,
        path_enabled=False,
        service_active=True,
        path_active=False,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
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
        import_ok=True,
        deployed=True,
        stale_path_unit=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert (
        fixtures["systemd_dir"] / "system_metrics-ingest.path"
    ).read_text(encoding="utf-8") == _expected_ingest_path_unit(fixtures)
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
        import_ok=True,
        deployed=True,
    )
    inode_before = fixtures["command_path"].stat().st_ino
    result = system_metrics_setup.task(
        _ctx(tmp_path, force=True, config=fixtures["config"])
    )
    assert result.success is True
    assert result.changed is True
    assert fixtures["command_path"].read_text(encoding="utf-8") == _expected_command(
        fixtures
    )
    assert fixtures["command_path"].stat().st_ino != inode_before
