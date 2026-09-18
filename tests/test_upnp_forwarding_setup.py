"""Task tests for upnp_forwarding_setup.

The task is exercised with temporary fixtures and a fake subprocess: the
repository clone, the unit templates, the venv, the system config and the
systemd unit directory are all temporary, and the systemctl answers come
from a recorded fake, so the real machine is never touched. The journal is
disabled by conftest.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

import pytest
from support import FakeProc, make_config, make_context

from pyntara import __version__
from pyntara.config import UpnpForwardingSetupConfig
from pyntara.context import Context
from pyntara.tasks import upnp_forwarding_setup
from pyntara.values import engine as engine_values

SERVICE_TEMPLATE = """\
[Unit]
Description=Router port forwarding
# Deployed by Pyntara $version
After=network-online.target local-fs.target
Wants=network-online.target

[Service]
Type=oneshot
StandardOutput=null
$exec_lines
"""

TIMER_TEMPLATE = """\
[Unit]
Description=Router port forwarding timer
# Deployed by Pyntara $version

[Timer]
OnBootSec=$boot_delay_seconds
OnUnitActiveSec=$interval_seconds
Unit=$service_unit_name

[Install]
WantedBy=timers.target
"""


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Path, Context]:
    """Point the task at temporary fixtures; return the fixture paths."""

    repo = tmp_path / "repo"
    task_data = repo / "task_data" / "upnp_forwarding_setup"
    task_data.mkdir(parents=True)
    (task_data / "upnp_forwarding.service").write_text(
        SERVICE_TEMPLATE, encoding="utf-8"
    )
    (task_data / "upnp_forwarding.timer").write_text(TIMER_TEMPLATE, encoding="utf-8")
    venv_dir = tmp_path / "usr" / "local" / "lib" / "pyntara" / "venv"
    venv_python = venv_dir / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    system_config = tmp_path / "etc" / "pyntara" / "config.toml"
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(engine_values, "SYSTEMD_UNIT_DIR", systemd_dir)
    config = make_config(
        system_metrics_venv_dir=venv_dir,
        system_metrics_system_config_path=system_config,
    )
    ctx = make_context(
        task_data_root=tmp_path,
        config=config,
        repo_root=repo,
        task_name="upnp_forwarding_setup",
    )
    return systemd_dir, venv_python, system_config, ctx


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = False,
    active: bool = False,
    failed: bool = False,
    start_ok: bool = True,
    venv_version: str | None = __version__,
) -> list[list[str]]:
    """Install the systemctl fake; return the recorded command calls.

    venv_version is the pyntara version the deployed interpreter reports;
    None makes that call fail, which is a deployment the task cannot read.
    """

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
        if command[0] == "systemctl" and command[1] == "start" and not start_ok:
            raise OSError("systemd is not running")
        if command[0].endswith("/python") and command[1] == "-c":
            if venv_version is None:
                return FakeProc(1)
            return FakeProc(0, f"{venv_version}\n")
        return FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _expected_service_unit(
    venv_python: Path,
    system_config: Path,
    cfg: UpnpForwardingSetupConfig,
    version: str = __version__,
) -> str:
    command = " ".join(
        [str(venv_python), "-m", cfg.service_module_name, str(system_config)]
    )
    return Template(SERVICE_TEMPLATE).substitute(
        exec_lines=f"ExecStart={command}", version=version
    )


def _expected_timer_unit(
    cfg: UpnpForwardingSetupConfig, version: str = __version__
) -> str:
    return Template(TIMER_TEMPLATE).substitute(
        boot_delay_seconds=cfg.timer_boot_delay_seconds,
        interval_seconds=cfg.timer_interval_seconds,
        service_unit_name=cfg.service_unit_name,
        version=version,
    )


def _deploy_units(systemd_dir: Path, ctx: Context) -> None:
    """Write the units the task would write, as an earlier run did."""

    cfg = ctx.config.upnp_forwarding_setup
    systemd_dir.mkdir(parents=True, exist_ok=True)
    (systemd_dir / cfg.service_unit_name).write_text(
        _expected_service_unit(
            ctx.config.system_metrics_setup.venv_dir
            / ctx.config.system_metrics_setup.venv_python_relative_path,
            ctx.config.system_metrics_setup.system_config_path,
            cfg,
        ),
        encoding="utf-8",
    )
    (systemd_dir / cfg.timer_unit_name).write_text(
        _expected_timer_unit(cfg), encoding="utf-8"
    )


def test_deploys_both_units_and_runs_the_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, venv_python, system_config, ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    calls = _install_fake(monkeypatch)
    result = upnp_forwarding_setup.task(ctx)
    assert result.success
    assert result.changed
    assert not result.warnings
    cfg = ctx.config.upnp_forwarding_setup
    assert (systemd_dir / cfg.service_unit_name).read_text(
        encoding="utf-8"
    ) == _expected_service_unit(venv_python, system_config, cfg)
    assert (systemd_dir / cfg.timer_unit_name).read_text(
        encoding="utf-8"
    ) == _expected_timer_unit(cfg)
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", cfg.timer_unit_name] in calls
    assert ["systemctl", "start", "--no-block", cfg.timer_unit_name] in calls
    assert ["systemctl", "start", "--no-block", cfg.service_unit_name] in calls


def test_skips_when_the_units_and_the_timer_are_in_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, _venv, _config, ctx = _install_fixtures(monkeypatch, tmp_path)
    _deploy_units(systemd_dir, ctx)
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    result = upnp_forwarding_setup.task(ctx)
    assert result.success
    assert not result.changed
    assert not result.warnings
    assert ["systemctl", "daemon-reload"] not in calls
    assert [
        "systemctl",
        "start",
        "--no-block",
        ctx.config.upnp_forwarding_setup.service_unit_name,
    ] not in calls


def test_the_units_carry_the_version_of_the_deployed_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The version in both units is the one the deployed interpreter
    # reports, not the version of the running installer: the units name the
    # code they will run.
    systemd_dir, _venv, _config, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, active=True, venv_version="0.3.999")
    result = upnp_forwarding_setup.task(ctx)
    assert result.success
    assert not result.warnings
    cfg = ctx.config.upnp_forwarding_setup
    service = (systemd_dir / cfg.service_unit_name).read_text(encoding="utf-8")
    timer = (systemd_dir / cfg.timer_unit_name).read_text(encoding="utf-8")
    assert "# Deployed by Pyntara 0.3.999" in service
    assert "# Deployed by Pyntara 0.3.999" in timer


def test_units_of_another_version_are_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A unit that names another version is stale even when everything else
    # matches, so it is written again and systemd is reloaded: that is what
    # carries an update of the code to the machine.
    systemd_dir, venv_python, system_config, ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    cfg = ctx.config.upnp_forwarding_setup
    systemd_dir.mkdir(parents=True)
    (systemd_dir / cfg.service_unit_name).write_text(
        _expected_service_unit(venv_python, system_config, cfg, version="0.0.1"),
        encoding="utf-8",
    )
    (systemd_dir / cfg.timer_unit_name).write_text(
        _expected_timer_unit(cfg, version="0.0.1"), encoding="utf-8"
    )
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    result = upnp_forwarding_setup.task(ctx)
    assert result.changed
    assert ["systemctl", "daemon-reload"] in calls
    assert (systemd_dir / cfg.service_unit_name).read_text(
        encoding="utf-8"
    ) == _expected_service_unit(venv_python, system_config, cfg)


def test_a_deployment_that_cannot_be_asked_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A venv that cannot be asked leaves the repository version in the
    # units and names the gap instead of passing the deployment off as the
    # new code.
    systemd_dir, _venv, _config, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, active=True, venv_version=None)
    result = upnp_forwarding_setup.task(ctx)
    assert result.success
    cfg = ctx.config.upnp_forwarding_setup
    service = (systemd_dir / cfg.service_unit_name).read_text(encoding="utf-8")
    assert f"# Deployed by Pyntara {__version__}" in service
    assert any("cannot read the version" in warning for warning in result.warnings)


def test_force_runs_the_service_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, _venv, _config, ctx = _install_fixtures(monkeypatch, tmp_path)
    _deploy_units(systemd_dir, ctx)
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    ctx = make_context(
        task_data_root=tmp_path,
        config=ctx.config,
        repo_root=tmp_path / "repo",
        task_name="upnp_forwarding_setup",
        force_tasks=frozenset({"upnp_forwarding_setup"}),
    )
    result = upnp_forwarding_setup.task(ctx)
    assert result.changed
    assert [
        "systemctl",
        "start",
        "--no-block",
        ctx.config.upnp_forwarding_setup.service_unit_name,
    ] in calls


def test_a_service_in_the_failed_state_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _systemd_dir, _venv, _config, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, failed=True)
    result = upnp_forwarding_setup.task(ctx)
    assert result.success
    assert any("failed state" in warning for warning in result.warnings)


def test_a_failed_command_is_a_warning_and_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _systemd_dir, _venv, _config, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, start_ok=False)
    result = upnp_forwarding_setup.task(ctx)
    assert result.success
    assert result.warnings


def test_a_missing_template_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The template of one unit is gone: that unit cannot be written, and
    # the timer that is installed still gets enabled and started.
    _systemd_dir, _venv, _config, ctx = _install_fixtures(monkeypatch, tmp_path)
    (
        tmp_path
        / "repo"
        / "task_data"
        / "upnp_forwarding_setup"
        / "upnp_forwarding.timer"
    ).unlink()
    calls = _install_fake(monkeypatch)
    result = upnp_forwarding_setup.task(ctx)
    assert result.success
    assert any("template" in warning for warning in result.warnings)
    assert [
        "systemctl",
        "enable",
        ctx.config.upnp_forwarding_setup.timer_unit_name,
    ] in calls
