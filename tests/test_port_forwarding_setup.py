"""Task tests for port_forwarding_setup.

The task is exercised with temporary fixtures and a fake subprocess: the
repository clone, the unit template, the venv, the system config and the
systemd unit directory are all temporary, and systemctl answers come from
a recorded fake, so the real machine is never touched. The journal is
disabled by conftest.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

import pytest
from support import FakeProc, make_config, make_context

from pyntara.context import Context
from pyntara.tasks import port_forwarding_setup

UNIT_TEMPLATE = """\
[Unit]
Description=Auto port forwarding
After=network-online.target local-fs.target
Wants=network-online.target

[Service]
Type=simple
Environment=PYNTARA_JOURNAL_IDENTIFIER=$journal_identifier
StandardOutput=null
Restart=on-failure
RestartSec=$restart_seconds
$exec_lines

[Install]
WantedBy=multi-user.target
"""


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Path, Context]:
    """Point the task at temporary fixtures; return the fixture paths."""

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    task_data = repo / "task_data" / "port_forwarding_setup"
    task_data.mkdir(parents=True)
    template = task_data / "auto_port_forwarding.service"
    template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    venv_dir = tmp_path / "usr" / "local" / "lib" / "pyntara" / "venv"
    venv_python = venv_dir / "bin" / "python"
    system_config = tmp_path / "etc" / "pyntara" / "config.toml"
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(port_forwarding_setup, "REPO_ROOT", repo)
    monkeypatch.setattr(port_forwarding_setup, "TEMPLATE_PATH", template)
    monkeypatch.setattr(port_forwarding_setup, "SYSTEMD_UNIT_DIR", systemd_dir)
    monkeypatch.setattr(port_forwarding_setup.time, "sleep", lambda seconds: None)
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_venv_dir=venv_dir,
        system_metrics_system_config_path=system_config,
        port_forwarding_state_file_path=tmp_path / "port_forwarding_state.json",
    )
    ctx = make_context(task_data_root=tmp_path, config=config)
    return systemd_dir, venv_python, system_config, ctx


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = False,
    active: bool = False,
    failed: bool = False,
) -> list[list[str]]:
    """Install the systemctl fake; return the recorded command calls."""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        del kwargs
        calls.append(list(command))
        if command[0] == "systemctl" and command[1] == "is-enabled":
            return FakeProc(0, "enabled\n") if enabled else FakeProc(1, "disabled")
        if command[0] == "systemctl" and command[1] == "is-active":
            return FakeProc(0, "active\n") if active else FakeProc(1, "inactive")
        if command[0] == "systemctl" and command[1] == "is-failed":
            return FakeProc(0, "failed\n") if failed else FakeProc(1, "inactive")
        return FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _expected_unit(
    venv_python: Path, system_config: Path, journal_identifier: str, restart_seconds: int
) -> str:
    """The unit the task must render for the given fixtures."""

    command = " ".join(
        [
            str(venv_python),
            "-m",
            "pyntara.port_forwarding",
            str(system_config),
        ]
    )
    return Template(UNIT_TEMPLATE).substitute(
        exec_lines=f"ExecStart={command}",
        journal_identifier=journal_identifier,
        restart_seconds=restart_seconds,
    )


def test_deploys_unit_and_starts_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, venv_python, system_config, ctx = _install_fixtures(monkeypatch, tmp_path)
    calls = _install_fake(monkeypatch, active=True)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert result.changed
    service = ctx.config.port_forwarding_setup.service_unit_name
    expected = _expected_unit(
        venv_python,
        system_config,
        ctx.config.port_forwarding_setup.journal_identifier,
        ctx.config.port_forwarding_setup.service_restart_seconds,
    )
    assert (systemd_dir / service).read_text(encoding="utf-8") == expected
    command_names = [tuple(command) for command in calls]
    assert ("systemctl", "daemon-reload") in command_names
    assert ("systemctl", "enable", service) in command_names
    assert ("systemctl", "restart", service) in command_names


def test_skips_when_already_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, venv_python, system_config, ctx = _install_fixtures(monkeypatch, tmp_path)
    service = ctx.config.port_forwarding_setup.service_unit_name
    expected = _expected_unit(
        venv_python,
        system_config,
        ctx.config.port_forwarding_setup.journal_identifier,
        ctx.config.port_forwarding_setup.service_restart_seconds,
    )
    systemd_dir.mkdir(parents=True)
    (systemd_dir / service).write_text(expected, encoding="utf-8")
    calls = _install_fake(monkeypatch, enabled=True)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert not result.changed
    assert not any(command[1] == "restart" for command in calls)


def test_force_rewrites_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, venv_python, system_config, ctx = _install_fixtures(monkeypatch, tmp_path)
    service = ctx.config.port_forwarding_setup.service_unit_name
    systemd_dir.mkdir(parents=True)
    (systemd_dir / service).write_text("stale\n", encoding="utf-8")
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    force_ctx = make_context(
        task_data_root=tmp_path,
        force_tasks=frozenset({"port_forwarding_setup"}),
        config=ctx.config,
    )
    result = port_forwarding_setup.task(force_ctx)
    assert result.success
    assert result.changed
    expected = _expected_unit(
        venv_python,
        system_config,
        ctx.config.port_forwarding_setup.journal_identifier,
        ctx.config.port_forwarding_setup.service_restart_seconds,
    )
    assert (systemd_dir / service).read_text(encoding="utf-8") == expected
    assert any(command[1] == "restart" for command in calls)


def test_force_resets_state_for_a_fresh_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Force mode removes the recorded port state before the restart, so
    # the service re-derives the desired port from the hostname.
    _, _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    state_path = ctx.config.port_forwarding_setup.state_file_path
    state_path.write_text(
        '{"169.58.51.98": {"30222": 46132}}\n', encoding="utf-8"
    )
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    force_ctx = make_context(
        task_data_root=tmp_path,
        force_tasks=frozenset({"port_forwarding_setup"}),
        config=ctx.config,
    )
    result = port_forwarding_setup.task(force_ctx)
    assert result.success
    assert result.changed
    assert not state_path.exists()
    assert any(command[1] == "restart" for command in calls)


def test_non_force_keeps_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A plain deploy must not touch the recorded ports, so the tunnel
    # stays stable across a routine restart.
    _, _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    state_path = ctx.config.port_forwarding_setup.state_file_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"169.58.51.98": {"30222": 46132}}\n', encoding="utf-8"
    )
    calls = _install_fake(monkeypatch, active=True)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert state_path.exists()
    assert any(command[1] == "restart" for command in calls)


def test_failed_after_start_is_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, failed=True)
    result = port_forwarding_setup.task(ctx)
    assert not result.success
    assert "failed state" in (result.error or "")


def test_inactive_clean_exit_is_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine whose vault has no port-forwarding data makes the service
    # exit cleanly right after a start; that is the intended no-op state,
    # not a failure.
    _, _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, active=False, failed=False)
    result = port_forwarding_setup.task(ctx)
    assert result.success


def test_missing_template_is_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    task_data = repo / "task_data" / "port_forwarding_setup"
    task_data.mkdir(parents=True)
    missing = task_data / "auto_port_forwarding.service"
    monkeypatch.setattr(port_forwarding_setup, "REPO_ROOT", repo)
    monkeypatch.setattr(port_forwarding_setup, "TEMPLATE_PATH", missing)
    monkeypatch.setattr(port_forwarding_setup, "SYSTEMD_UNIT_DIR", tmp_path / "systemd")
    ctx = make_context(
        task_data_root=tmp_path,
        config=make_config(task_data_root=tmp_path),
    )
    result = port_forwarding_setup.task(ctx)
    assert not result.success
    assert "template" in (result.error or "")
