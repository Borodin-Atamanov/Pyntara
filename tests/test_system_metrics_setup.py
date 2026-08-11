"""Unit tests for the system_metrics_setup task.

All external resources (uv, the venv python, systemctl, filesystem paths)
are mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The unit template is rendered from a
fixture, so the tests never read the repository template.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

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
Environment=PYNTARA_JOURNAL_IDENTIFIER=system_metrics
StandardOutput=null
Restart=on-failure
$exec_lines

[Install]
WantedBy=multi-user.target
"""


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
    venv_script_ok: bool = True,
) -> dict[str, Path]:
    """Point the task at temporary fixtures; return the fixture paths.

    The repository clone is a temporary directory holding config.toml and
    the unit template; the venv, the system config and the unit directory
    are temporary paths as well, so the real machine is never touched.
    venv_script_ok controls whether the console script is created next to
    the venv python: a venv that predates the entry point has the python
    but no script.
    """

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    source_config = repo / "config.toml"
    source_config.write_text(
        "[system_metrics_setup]\ncheck_interval_seconds = 300\n", encoding="utf-8"
    )
    template = repo / "task_data" / "system_metrics_setup" / "system_metrics.service"
    template.parent.mkdir(parents=True)
    template.write_text(UNIT_TEMPLATE, encoding="utf-8")
    venv_dir = tmp_path / "usr" / "local" / "lib" / "pyntara" / "venv"
    venv_python = venv_dir / "bin" / "python"
    venv_script = venv_dir / "bin" / system_metrics_setup.COMMIT_COMMAND_NAME
    if venv_ok:
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
        if venv_script_ok:
            venv_script.write_text("#!/bin/sh\n", encoding="utf-8")
    command_path = tmp_path / "usr" / "local" / "bin" / system_metrics_setup.COMMIT_COMMAND_NAME
    system_config_dir = tmp_path / "etc" / "pyntara"
    system_config = system_config_dir / "config.toml"
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(system_metrics_setup, "REPO_ROOT", repo)
    monkeypatch.setattr(system_metrics_setup, "TEMPLATE_PATH", template)
    monkeypatch.setattr(system_metrics_setup, "SYSTEMD_UNIT_DIR", systemd_dir)
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_venv_dir=venv_dir,
        system_metrics_system_config_path=system_config,
        system_metrics_command_path=command_path,
    )
    return {
        "repo": repo,
        "source_config": source_config,
        "template": template,
        "venv_dir": venv_dir,
        "venv_python": venv_python,
        "venv_script": venv_script,
        "command_path": command_path,
        "system_config": system_config,
        "systemd_dir": systemd_dir,
        "config": config,
    }


def _expected_unit(fixtures: dict[str, Path]) -> str:
    """The unit file the task must render for the given fixtures."""

    command = " ".join(
        [
            str(fixtures["venv_python"]),
            "-m",
            "pyntara.metrics",
            str(fixtures["system_config"]),
        ]
    )
    return UNIT_TEMPLATE.replace("$exec_lines", f"ExecStart={command}")


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool,
    active: bool,
    import_ok: bool,
    uv_available: bool = True,
    fail: Callable[[list[str]], bool] | None = None,
    on_pip_install: Callable[[], None] | None = None,
) -> list[list[str]]:
    """Install subprocess and uv fakes; return the recorded command calls.

    systemctl is-enabled and is-active answer from the flags, the venv
    python import answers from import_ok, and uv commands succeed unless
    matched by fail. on_pip_install runs after a successful uv pip
    install, so a test can simulate the console script appearing in the
    venv.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if fail is not None and fail(command):
            raise subprocess.CalledProcessError(1, command)
        if command[0] == "systemctl" and command[1] == "is-enabled":
            if enabled:
                return _FakeProc(0, "enabled\n")
            return _FakeProc(1, "disabled")
        if command[0] == "systemctl" and command[1] == "is-active":
            if active:
                return _FakeProc(0, "active\n")
            return _FakeProc(1, "inactive")
        if command[0].endswith("/python") and command[1] == "-c":
            if import_ok:
                return _FakeProc(0)
            return _FakeProc(1)
        if (
            command[0] == "uv"
            and command[1] == "pip"
            and command[2] == "install"
            and on_pip_install is not None
        ):
            on_pip_install()
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
    enabled: bool = False,
    active: bool = False,
    import_ok: bool = False,
    deployed: bool = False,
    command_linked: bool = True,
    uv_available: bool = True,
    venv_script_ok: bool = True,
    create_script_on_install: bool = False,
    fail: Callable[[list[str]], bool] | None = None,
) -> tuple[dict[str, Path], list[list[str]]]:
    """Fixtures plus a fake; when deployed, config, unit and link match.

    create_script_on_install makes the fake uv pip install create the
    console script in the venv, as the real installer does; it simulates
    the package refresh of a venv that predates the entry point.
    """

    fixtures = _install_fixtures(
        monkeypatch,
        tmp_path,
        venv_ok=deployed or import_ok,
        venv_script_ok=venv_script_ok,
    )
    if deployed:
        fixtures["system_config"].parent.mkdir(parents=True)
        fixtures["system_config"].write_text(
            fixtures["source_config"].read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        fixtures["systemd_dir"].mkdir(parents=True)
        (fixtures["systemd_dir"] / system_metrics_setup.SERVICE_NAME).write_text(
            _expected_unit(fixtures), encoding="utf-8"
        )
        if command_linked:
            fixtures["command_path"].parent.mkdir(parents=True)
            fixtures["command_path"].symlink_to(fixtures["venv_script"])

    def on_pip_install() -> None:
        if create_script_on_install:
            fixtures["venv_script"].parent.mkdir(parents=True, exist_ok=True)
            fixtures["venv_script"].write_text("#!/bin/sh\n", encoding="utf-8")

    calls = _install_fake(
        monkeypatch,
        enabled=enabled,
        active=active,
        import_ok=import_ok,
        uv_available=uv_available,
        fail=fail,
        on_pip_install=on_pip_install,
    )
    return fixtures, calls


def test_deploys_service_and_starts_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Nothing is deployed: the task creates the venv, installs the package
    # from the clone, copies the config, writes the unit, enables and
    # starts the service.
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
    unit = fixtures["systemd_dir"] / system_metrics_setup.SERVICE_NAME
    assert unit.read_text(encoding="utf-8") == _expected_unit(fixtures)
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "system_metrics.service"] in calls
    assert ["systemctl", "start", "system_metrics.service"] in calls
    assert "System Metrics service deployed" in (result.message or "")
    assert fixtures["command_path"].is_symlink()
    assert os.readlink(fixtures["command_path"]) == str(fixtures["venv_script"])
    captured = capsys.readouterr()
    assert "creating venv" in captured.out


def test_skips_when_already_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv imports pyntara, the config and the unit match and the
    # service is enabled: only status queries run, nothing changes.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
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
    # is reinstalled with --reinstall, the config and unit rewritten, the
    # service enabled and restarted.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
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
    assert ["systemctl", "restart", "system_metrics.service"] in calls
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
    # The venv, config and unit are in place, only the boot service is
    # missing: no venv or config work happens, the service is enabled and
    # started.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=False,
        active=False,
        import_ok=True,
        deployed=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "uv" for call in calls)
    assert ["systemctl", "enable", "system_metrics.service"] in calls
    assert ["systemctl", "start", "system_metrics.service"] in calls
    assert fixtures["system_config"].read_text(encoding="utf-8") == (
        fixtures["source_config"].read_text(encoding="utf-8")
    )


def test_config_change_restarts_running_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv and unit are fine but the system config is stale and the
    # service is already running: it must be restarted to pick up the new
    # config, not started again.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
        import_ok=True,
        deployed=False,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert ["systemctl", "restart", "system_metrics.service"] in calls
    assert not any(call == ["systemctl", "start", "system_metrics.service"] for call in calls)
    assert fixtures["system_config"].read_text(encoding="utf-8") == (
        fixtures["source_config"].read_text(encoding="utf-8")
    )


def test_only_command_missing_links_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv, config, unit and service enablement are in place, only the
    # command symlink is missing: no venv or systemd work happens, the
    # symlink is created.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
        import_ok=True,
        deployed=True,
        command_linked=False,
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
    assert fixtures["command_path"].is_symlink()
    assert os.readlink(fixtures["command_path"]) == str(fixtures["venv_script"])


def test_missing_command_script_reinstalls_venv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The venv imports pyntara but the console script was never created
    # (the venv predates the entry point): the package is reinstalled
    # without --reinstall so the script appears, then the command symlink
    # is created; the service itself is untouched.
    fixtures, calls = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
        import_ok=True,
        deployed=True,
        command_linked=False,
        venv_script_ok=False,
        create_script_on_install=True,
    )
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert any(
        call[0] == "uv" and call[1] == "pip" and call[2] == "install"
        and "--reinstall" not in call
        for call in calls
    )
    assert not any(
        call[0] == "systemctl"
        and call[1] in ("daemon-reload", "enable", "start", "restart")
        for call in calls
    )
    assert fixtures["venv_script"].is_file()
    assert fixtures["command_path"].is_symlink()
    assert os.readlink(fixtures["command_path"]) == str(fixtures["venv_script"])


def test_wrong_symlink_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A symlink that points at the wrong target is replaced with the
    # correct one; the service itself is untouched.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
        import_ok=True,
        deployed=True,
        command_linked=False,
    )
    wrong_target = tmp_path / "elsewhere"
    wrong_target.write_text("#!/bin/sh\n", encoding="utf-8")
    fixtures["command_path"].parent.mkdir(parents=True)
    fixtures["command_path"].symlink_to(wrong_target)
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert os.readlink(fixtures["command_path"]) == str(fixtures["venv_script"])
    assert "replaced" in (result.message or "")


def test_command_regular_file_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A regular file occupying the command path is replaced: the path is
    # explicitly configured, so a foreign file is a conflict the operator
    # wants resolved, not a reason to abort.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
        import_ok=True,
        deployed=True,
        command_linked=False,
    )
    fixtures["command_path"].parent.mkdir(parents=True)
    fixtures["command_path"].write_text("foreign\n", encoding="utf-8")
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is True
    assert result.changed is True
    assert fixtures["command_path"].is_symlink()
    assert os.readlink(fixtures["command_path"]) == str(fixtures["venv_script"])
    assert "replaced" in (result.message or "")


def test_command_directory_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory on the command path cannot be replaced (no recursive
    # removal): the task fails with a clear error and leaves the
    # directory alone.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
        import_ok=True,
        deployed=True,
        command_linked=False,
    )
    fixtures["command_path"].mkdir(parents=True)
    result = system_metrics_setup.task(_ctx(tmp_path, config=fixtures["config"]))
    assert result.success is False
    assert "directory" in (result.error or "")
    assert fixtures["command_path"].is_dir()


def test_force_recreates_command_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # In force mode the command symlink is recreated even when it already
    # points at the venv script.
    fixtures, _ = _deploy_fixture(
        monkeypatch,
        tmp_path,
        enabled=True,
        active=True,
        import_ok=True,
        deployed=True,
    )
    inode_before = fixtures["command_path"].lstat().st_ino
    result = system_metrics_setup.task(
        _ctx(tmp_path, force=True, config=fixtures["config"])
    )
    assert result.success is True
    assert result.changed is True
    assert os.readlink(fixtures["command_path"]) == str(fixtures["venv_script"])
    assert fixtures["command_path"].lstat().st_ino != inode_before
