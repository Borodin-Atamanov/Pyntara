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
from types import SimpleNamespace

import pytest
from support import FakeProc, make_context

from pyntara import __version__
from pyntara.context import Context
from pyntara.tasks import port_forwarding_setup
from pyntara.values import engine as engine_values
from pyntara.values import port_forwarding_setup as values
from pyntara.values import ssh_daemon_setup as ssh_daemon_values
from pyntara.values import system_metrics_setup as metrics_values

UNIT_TEMPLATE = """\
[Unit]
Description=Auto port forwarding
# Deployed by Pyntara $version
After=network-online.target local-fs.target
Wants=network-online.target

[Service]
Type=simple
StandardOutput=null
Restart=on-failure
RestartSec=$restart_seconds
$exec_lines

[Install]
WantedBy=multi-user.target
"""


def _install_fixtures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Context]:
    """Point the task at temporary fixtures; return the fixture paths."""

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    task_data = repo / "task_data" / "port_forwarding_setup"
    task_data.mkdir(parents=True)
    template = task_data / "auto_port_forwarding.service"
    template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    venv_dir = tmp_path / "usr" / "local" / "lib" / "pyntara" / "venv"
    venv_python = venv_dir / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(engine_values, "SYSTEMD_UNIT_DIR", systemd_dir)
    monkeypatch.setattr(port_forwarding_setup.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        values, "STATE_FILE_PATH", tmp_path / "port_forwarding_state.json"
    )
    monkeypatch.setattr(metrics_values, "VENV_DIR", venv_dir)
    ctx = make_context(
        task_data_root=tmp_path,
        repo_root=repo,
        task_name="port_forwarding_setup",
    )
    return systemd_dir, venv_python, ctx


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = False,
    active: bool = False,
    failed: bool = False,
    last_result: str = "success",
    venv_version: str | None = __version__,
) -> list[list[str]]:
    """Install the systemctl fake; return the recorded command calls.

    venv_version is the pyntara version the deployed interpreter reports;
    None makes that call fail, which is a deployment the task cannot read.
    last_result is the word systemd reports for the last run of the unit.
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
        if command[0] == "systemctl" and command[1] == "show":
            return FakeProc(0, f"{last_result}\n")
        if command[0].endswith("/python") and command[1] == "-c":
            if venv_version is None:
                return FakeProc(1)
            return FakeProc(0, f"{venv_version}\n")
        return FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def _expected_unit(
    venv_python: Path,
    version: str = __version__,
) -> str:
    """The unit the task must render for the given fixtures."""

    command = " ".join(
        [
            str(venv_python),
            "-m",
            values.SERVICE_MODULE_NAME,
        ]
    )
    return Template(UNIT_TEMPLATE).substitute(
        exec_lines=f"ExecStart={command}",
        restart_seconds=values.SERVICE_RESTART_SECONDS,
        version=version,
    )


def test_deploys_unit_and_starts_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, venv_python, ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    calls = _install_fake(monkeypatch, active=True)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert result.changed
    service = values.SERVICE_UNIT_NAME
    expected = _expected_unit(venv_python)
    assert (systemd_dir / service).read_text(encoding="utf-8") == expected
    command_names = [tuple(command) for command in calls]
    assert ("systemctl", "daemon-reload") in command_names
    assert ("systemctl", "enable", service) in command_names
    assert ("systemctl", "restart", service) in command_names


def test_service_exec_line_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The line the deployed unit starts with is a declared value: another
    # command in the values is exactly what the unit runs, with the venv
    # interpreter, the module and the config path in their placeholders.
    monkeypatch.setattr(
        values,
        "MODULE_RUN_COMMAND",
        ("myrun", "-m", "{module}"),
    )
    template = tmp_path / "auto_port_forwarding.service"
    template.write_text(
        "[Service]\n$exec_lines\nRestartSec=$restart_seconds\n",
        encoding="utf-8",
    )
    unit = port_forwarding_setup._render_service_unit(
        template,
        Path("/venv/bin/python"),
        values.SERVICE_MODULE_NAME,
        values.SERVICE_RESTART_SECONDS,
        "0.3.516",
    )
    assert (
        f"ExecStart=myrun -m {values.SERVICE_MODULE_NAME}"
        in unit
    )
    assert f"RestartSec={values.SERVICE_RESTART_SECONDS}" in unit


def test_the_unit_carries_the_version_of_the_deployed_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The version in the unit is the one the deployed interpreter reports,
    # not the version of the running installer: the unit names the code it
    # will run, and that is the version a reader of the machine and the
    # comparison of the next run both look at.
    systemd_dir, _venv_python, ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    _install_fake(monkeypatch, active=True, venv_version="0.3.999")
    result = port_forwarding_setup.task(ctx)
    assert result.success
    service = values.SERVICE_UNIT_NAME
    unit = (systemd_dir / service).read_text(encoding="utf-8")
    assert "# Deployed by Pyntara 0.3.999" in unit
    assert not result.warnings


def test_a_unit_of_another_version_is_rewritten_and_the_service_restarted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The defect this prevents: the code under the unit was updated while
    # the unit stayed the same, so the task skipped the service and the
    # machine kept running the old code. A unit that differs in its
    # version line alone is stale, so it is written again and the service
    # restarted.
    systemd_dir, venv_python, ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    service = values.SERVICE_UNIT_NAME
    systemd_dir.mkdir(parents=True)
    older = _expected_unit(venv_python).replace(
        f"# Deployed by Pyntara {__version__}", "# Deployed by Pyntara 0.0.1"
    )
    (systemd_dir / service).write_text(older, encoding="utf-8")
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert result.changed
    assert (systemd_dir / service).read_text(encoding="utf-8") == _expected_unit(
        venv_python
    )
    assert any(command[1] == "restart" for command in calls)


def test_a_deployment_that_cannot_be_asked_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A venv that cannot be asked leaves the repository version in the
    # unit and names the gap, so the missing refresh is visible in the
    # install log instead of being passed off as the new code.
    systemd_dir, _venv_python, ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    _install_fake(monkeypatch, active=True, venv_version=None)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    service = values.SERVICE_UNIT_NAME
    unit = (systemd_dir / service).read_text(encoding="utf-8")
    assert f"# Deployed by Pyntara {__version__}" in unit
    assert any("cannot read the version" in warning for warning in result.warnings)


def test_renders_the_configured_module_and_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit runs the declared module and the task drives the unit with
    # the declared commands, so a renamed module or a command that grew an
    # argument is a value change and never a code change.
    systemd_dir, venv_python, ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(values, "SERVICE_MODULE_NAME", "other.module")
    monkeypatch.setattr(
        values,
        "SYSTEMCTL_RESTART_COMMAND",
        (
            "systemctl",
            "restart",
            "{service_unit_name}",
            "--no-block",
        ),
    )
    calls = _install_fake(monkeypatch, active=True)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    unit = (systemd_dir / values.SERVICE_UNIT_NAME).read_text(encoding="utf-8")
    expected = _expected_unit(venv_python)
    assert unit == expected
    assert "-m other.module" in unit
    assert (
        "systemctl",
        "restart",
        values.SERVICE_UNIT_NAME,
        "--no-block",
    ) in [tuple(command) for command in calls]


def test_skips_when_already_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, venv_python, _ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    service = values.SERVICE_UNIT_NAME
    expected = _expected_unit(venv_python)
    systemd_dir.mkdir(parents=True)
    (systemd_dir / service).write_text(expected, encoding="utf-8")
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    result = port_forwarding_setup.task(_ctx)
    assert result.success
    assert not result.changed
    assert not any(command[1] == "restart" for command in calls)


def test_restarts_when_deployed_but_inactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A deployed and enabled unit whose service is not running is
    # restarted: the service exits cleanly when the vault carries no
    # port-forwarding data, and local_vault_setup may have synced the data
    # since, so a restart lets it re-read the vault and establish tunnels.
    systemd_dir, venv_python, ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    service = values.SERVICE_UNIT_NAME
    expected = _expected_unit(venv_python)
    systemd_dir.mkdir(parents=True)
    (systemd_dir / service).write_text(expected, encoding="utf-8")
    calls = _install_fake(monkeypatch, enabled=True, active=False)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert result.changed
    assert any(command[1] == "restart" for command in calls)


def test_force_rewrites_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    systemd_dir, venv_python, _ctx = _install_fixtures(
        monkeypatch, tmp_path
    )
    service = values.SERVICE_UNIT_NAME
    systemd_dir.mkdir(parents=True)
    (systemd_dir / service).write_text("stale\n", encoding="utf-8")
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    force_ctx = make_context(
        task_data_root=tmp_path,
        force_tasks=frozenset({"port_forwarding_setup"}),
        task_name="port_forwarding_setup",
    )
    result = port_forwarding_setup.task(force_ctx)
    assert result.success
    assert result.changed
    expected = _expected_unit(venv_python)
    assert (systemd_dir / service).read_text(encoding="utf-8") == expected
    assert any(command[1] == "restart" for command in calls)


def test_the_state_file_is_never_touched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The task owns the unit, not the ports: the service writes the state
    # file after every accepted port, so neither a plain deploy nor a
    # forced one removes it, and a routine restart therefore keeps the
    # ports the machine asked for.
    _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    state_path = values.STATE_FILE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    written = '{"169.58.51.98": {"30222": 46132}}\n'
    state_path.write_text(written, encoding="utf-8")
    calls = _install_fake(monkeypatch, active=True)
    force_ctx = make_context(
        task_data_root=tmp_path,
        force_tasks=frozenset({"port_forwarding_setup"}),
        task_name="port_forwarding_setup",
    )
    plain = port_forwarding_setup.task(ctx)
    forced = port_forwarding_setup.task(force_ctx)
    assert plain.success
    assert forced.success
    assert state_path.read_text(encoding="utf-8") == written
    assert any(command[1] == "restart" for command in calls)


def test_failed_after_start_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The service entered the failed state after the start: the reason is a
    # warning of a completed task and the deployed unit stays in place.
    _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, failed=True)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert any("failed state" in warning for warning in result.warnings)


def test_inactive_clean_exit_is_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine whose vault has no port-forwarding data makes the service
    # exit cleanly right after a start; that is the intended no-op state,
    # not a failure.
    _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, active=False, failed=False)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert result.warnings == ()


def test_a_service_that_keeps_restarting_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A service that exited nonzero is neither active nor failed while systemd
    # waits for the next attempt, so only the result of the last run shows the
    # loop; the run must report it instead of passing a deployment off as
    # working while the machine forwards nothing.
    _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, active=False, failed=False, last_result="exit-code")
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert any("did not stay up" in warning for warning in result.warnings)


def test_a_passphrase_that_decrypts_no_key_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A machine whose vault passphrase decrypts no deployed key forwards
    # nothing: the service connects to nothing at every start, so the run must
    # say it instead of reporting a deployment that works.
    _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, active=True)
    root_ssh = tmp_path / "root-ssh"
    root_ssh.mkdir(parents=True)
    key = root_ssh / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME
    key.write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(ssh_daemon_values, "ROOT_SSH_DIR", root_ssh)
    monkeypatch.setattr(
        port_forwarding_setup.metrics,
        "open_runtime_vault",
        lambda: SimpleNamespace(
            find_groups=lambda name, first: SimpleNamespace(
                entries=[SimpleNamespace(url="169.58.51.98")]
            ),
            find_entries=lambda title, first: SimpleNamespace(password="a-passphrase"),
        ),
    )
    monkeypatch.setattr(
        port_forwarding_setup.port_forwarding,
        "passphrase_decrypts_key",
        lambda *args, **kwargs: False,
    )
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert any("does not decrypt" in warning for warning in result.warnings)


def test_a_passphrase_that_decrypts_the_key_is_not_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The healthy machine ends the task without that warning, so the message
    # keeps its meaning for the machines where it appears.
    _, _, ctx = _install_fixtures(monkeypatch, tmp_path)
    _install_fake(monkeypatch, active=True)
    root_ssh = tmp_path / "root-ssh"
    root_ssh.mkdir(parents=True)
    key = root_ssh / ssh_daemon_values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME
    key.write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(ssh_daemon_values, "ROOT_SSH_DIR", root_ssh)
    monkeypatch.setattr(
        port_forwarding_setup.metrics,
        "open_runtime_vault",
        lambda: SimpleNamespace(
            find_groups=lambda name, first: SimpleNamespace(
                entries=[SimpleNamespace(url="169.58.51.98")]
            ),
            find_entries=lambda title, first: SimpleNamespace(password="a-passphrase"),
        ),
    )
    monkeypatch.setattr(
        port_forwarding_setup.port_forwarding,
        "passphrase_decrypts_key",
        lambda *args, **kwargs: True,
    )
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert not any("does not decrypt" in warning for warning in result.warnings)


def test_missing_template_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit template is missing: the reason is a warning of a completed
    # task, because the service that is installed on the machine is still
    # enabled and restarted.
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    task_data = repo / "task_data" / "port_forwarding_setup"
    task_data.mkdir(parents=True)
    ctx = make_context(
        task_data_root=tmp_path,
        repo_root=repo,
    )
    calls = _install_fake(monkeypatch, enabled=True, active=True)
    result = port_forwarding_setup.task(ctx)
    assert result.success
    assert any("template" in warning for warning in result.warnings)
    service = values.SERVICE_UNIT_NAME
    assert (
        "systemctl",
        "restart",
        service,
    ) in [tuple(command) for command in calls]
