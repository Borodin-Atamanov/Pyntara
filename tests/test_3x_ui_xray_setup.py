"""Unit tests for the three_x_ui_xray_setup task.

All external resources (subprocess, filesystem paths) are mocked via
monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The task wraps the official installer,
so the tests fake the GitHub releases API and the subprocess calls and
record the commands, verifying that the installer is invoked only when
the target state is not already reached. Stage 2 dependencies (panel
REST API, runtime vault) are also mocked.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.context import Context

xui = importlib.import_module("pyntara.tasks.three_x_ui_xray_setup")

TAG = "3.7.0"


def _release_json(tag: str = TAG) -> str:
    """The GitHub releases API payload used by the curl fake."""

    return json.dumps({"tag_name": f"v{tag}", "assets": []})


def _stage2_fake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    login_ok: bool = True,
    vault_ok: bool = True,
) -> None:
    """Mock stage 2 dependencies: panel API and runtime vault.

    Creates a fake install-result.env in tmp_path, mocks the panel
    client to return login_ok, and mocks the runtime vault opener to
    return a fake PyKeePass when vault_ok is True.
    """

    # Create a fake install-result.env in the tmp_path location.
    env_path = tmp_path / "etc" / "x-ui" / "install-result.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "XUI_USERNAME=admin\n"
        "XUI_PASSWORD=secret\n"
        "XUI_PANEL_PORT=3579\n"
        "XUI_WEB_BASE_PATH=/xui\n"
        "XUI_API_TOKEN=tok123\n"
        "XUI_DB_TYPE=sqlite\n",
        encoding="utf-8",
    )

    # Mock the panel client.
    monkeypatch.setattr(
        "pyntara.xui.login_and_verify",
        lambda _cfg, _env, _timeout: login_ok,
    )

    # Mock the runtime vault opener.
    if vault_ok:
        fake_kp = Mock()
        fake_kp.find_entries.return_value = None
        fake_kp.root_group = Mock()
        fake_kp.add_entry = Mock()
        fake_kp.save = Mock()
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda _cfg: fake_kp)
    else:
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda _cfg: None)


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    check_attempts: int = 2,
    retry_delay: int = 0,
) -> Context:
    """Context with a small safe config; the real file is never touched."""

    return make_context(
        install_mode="server",
        force_tasks=frozenset({"three_x_ui_xray_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=make_config(
            task_data_root=tmp_path,
            cli_tools_packages=("mc",),
            add_extra_repos_components=("universe",),
            swapfile_path=tmp_path / "swapfile",
            three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
            three_x_ui_start_check_attempts=check_attempts,
            three_x_ui_start_check_retry_delay_seconds=retry_delay,
            three_x_ui_install_result_env_path=tmp_path / "etc" / "x-ui" / "install-result.env",
        ),
    )


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    release_json: str = _release_json(),
    install_dir: Path = Path("/usr/local/x-ui"),
    installed_version: str | None = None,
    missing_binary: bool = False,
    enabled: bool = False,
    active: bool = False,
    active_becomes: bool = True,
    installer_fails: bool = False,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    curl answers the release API and writes the fixture installer script,
    bash runs it (failing when installer_fails), systemctl reports the
    enabled and active state from the flags, and the version query
    answers installed_version. With active_becomes, the service turns
    active after the installer runs; without it, the readiness loop runs
    out. With missing_binary, the version query raises FileNotFoundError
    like a real missing executable.
    """

    calls: list[list[str]] = []
    started = False

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal started
        del kwargs
        calls.append(list(command))
        if command[0] == "curl":
            if "--output" in command:
                path = Path(command[command.index("--output") + 1])
                path.write_text("#!/bin/sh\necho fake installer\n", encoding="utf-8")
            return _FakeProc(0, release_json)
        if command[0] == "bash":
            started = True
            if installer_fails:
                raise subprocess.CalledProcessError(1, command)
            return _FakeProc(0)
        if command[0] == str(install_dir / "x-ui"):
            if missing_binary:
                raise FileNotFoundError(command[0])
            if installed_version is None:
                return _FakeProc(1, "")
            return _FakeProc(0, f"{installed_version}\n")
        if command[0] == "systemctl":
            if command[1] == "is-enabled":
                if enabled:
                    return _FakeProc(0, "enabled\n")
                return _FakeProc(1, "disabled\n")
            if command[1] == "is-active":
                if active or (active_becomes and started):
                    return _FakeProc(0, "active\n")
                return _FakeProc(1, "inactive\n")
            return _FakeProc(0)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    return calls


def test_already_configured_does_not_run_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installed version equals the newest release and the service is
    # enabled and active: the task returns done with changed=False and
    # never invokes the official installer. Stage 2 runs and succeeds.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=TAG,
        enabled=True,
        active=True,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.message == "already configured"
    assert not any(call[0] == "bash" for call in calls)


def test_installs_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 3x-ui is not installed and the service is not enabled or active:
    # the task downloads the official installer, runs it non-interactively
    # and waits for the service to become active. Stage 2 runs after.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=None,
        enabled=False,
        active=False,
        active_becomes=True,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert TAG in (result.message or "")
    assert any(call[0] == "bash" for call in calls)


def test_installs_new_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An older release is installed: the task runs the installer and
    # waits for the service to become active again. Stage 2 runs after.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version="3.6.0",
        enabled=True,
        active=False,
        active_becomes=True,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "bash" for call in calls)


def test_restarts_inactive_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The same version is installed but the service is inactive: the
    # target state is not reached, so the installer runs to bring it up.
    # Stage 2 runs after.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=TAG,
        enabled=True,
        active=False,
        active_becomes=True,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "bash" for call in calls)


def test_force_runs_installer_when_already_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Force mode reruns the installer even when the same version is
    # installed and the service is enabled and active. Stage 2 runs after.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, force=True)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=TAG,
        enabled=True,
        active=True,
        active_becomes=True,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "bash" for call in calls)


def test_installer_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The official installer exits nonzero: the task reports the failure
    # as an error result, which the runner converts to a warning.
    # Stage 2 is not reached because the installer failed.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=None,
        enabled=False,
        active=False,
        installer_fails=True,
    )
    result = xui.task(ctx)
    assert result.success is False
    assert "installer failed" in (result.error or "")
    assert any(call[0] == "bash" for call in calls)


def test_service_never_active_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installer ran but the service never becomes active within the
    # readiness loop: the task reports an error. Stage 2 is not reached.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, check_attempts=1)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=None,
        enabled=False,
        active=False,
        active_becomes=False,
    )
    result = xui.task(ctx)
    assert result.success is False
    assert "did not become active" in (result.error or "")
    assert any(call[0] == "bash" for call in calls)


def test_release_json_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The GitHub releases API is unreachable: the task reports the
    # failure without ever running the installer. Stage 2 is not reached.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        if command[0] == "curl":
            return _FakeProc(7, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = xui.task(ctx)
    assert result.success is False
    assert "cannot fetch" in (result.error or "")


def test_stage2_login_failure_reports_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installer succeeded but stage 2 login fails: the task returns
    # success with warnings.
    _stage2_fake(monkeypatch, tmp_path, login_ok=False)
    ctx = _ctx(tmp_path)
    _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=None,
        enabled=False,
        active=False,
        active_becomes=True,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert result.warnings is not None
    assert any("login failed" in w for w in result.warnings)


def test_stage2_vault_unavailable_reports_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installer and login succeeded but the runtime vault is
    # unavailable: the task returns success with warnings.
    _stage2_fake(monkeypatch, tmp_path, vault_ok=False)
    ctx = _ctx(tmp_path)
    _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=None,
        enabled=False,
        active=False,
        active_becomes=True,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert result.warnings is not None
    assert any("vault unavailable" in w for w in result.warnings)
