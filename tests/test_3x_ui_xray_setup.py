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
from typing import cast
from unittest.mock import Mock

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.config import Config, ThreeXuiXraySetupConfig
from pyntara.context import Context
from pyntara.models import TaskResult
from pyntara.public_address import PublicAddresses
from pyntara.utils import curl_flags

xui = importlib.import_module("pyntara.tasks.three_x_ui_xray_setup")

TAG = "3.7.0"


def _addresses(
    ipv4: tuple[str, ...] = (), ipv6: tuple[str, ...] = ()
) -> PublicAddresses:
    """The value the shared address helper returns in the task tests."""

    return PublicAddresses(ipv4=ipv4, ipv6=ipv6)


def _release_json(tag: str = TAG) -> str:
    """The GitHub releases API payload used by the curl fake."""

    return json.dumps({"tag_name": f"v{tag}", "assets": []})


def _stage2_fake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    login_ok: bool = True,
    vault_ok: bool = True,
    inbound_exists: bool = False,
    create_inbound_ok: bool = True,
    keygen_ok: bool = True,
) -> None:
    """Mock stage 2 and stage 3 dependencies: panel API and runtime vault.

    Creates a fake install-result.env in tmp_path, mocks the panel
    client to return login_ok, and mocks the runtime vault opener to
    return a fake PyKeePass when vault_ok is True. Stage 3 mocks:
    inbound_exists controls whether find_inbound_by_port returns an
    existing inbound; create_inbound_ok controls whether create_inbound
    succeeds; keygen_ok controls whether generate_reality_key returns a
    keypair.
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

    # Mock the panel client for stage 2.
    monkeypatch.setattr(
        "pyntara.xui.login_and_verify",
        lambda _cfg, _env, _timeout: login_ok,
    )

    # Mock stage 3 API functions.
    if inbound_exists:
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port",
            lambda _cfg, _env, _port, _timeout: {"id": 1, "port": _port, "protocol": "vless"},
        )
    else:
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port",
            lambda _cfg, _env, _port, _timeout: None,
        )

    if keygen_ok:
        monkeypatch.setattr(
            "pyntara.xui.generate_reality_key",
            lambda _cfg, _env, _timeout: ("priv123", "pub123"),
        )
    else:
        monkeypatch.setattr(
            "pyntara.xui.generate_reality_key",
            lambda _cfg, _env, _timeout: None,
        )

    monkeypatch.setattr(
        "pyntara.xui.create_inbound",
        lambda _cfg, _env, _payload, _timeout: (create_inbound_ok, "inbound created"),
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
            three_x_ui_cert_dir=tmp_path / "cert",
            three_x_ui_self_signed_cert_dir=tmp_path / "selfsigned",
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
    captured_env: list[dict[str, str]] | None = None,
    mock_stage_ssl: bool = True,
    mock_takeover: bool = True,
    mock_settings: bool = True,
    mock_connection: bool = True,
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    curl answers the release API and writes the fixture installer script,
    bash runs it (failing when installer_fails), systemctl reports the
    enabled and active state from the flags, and the version query
    answers installed_version. With active_becomes, the service turns
    active after the installer runs; without it, the readiness loop runs
    out. With missing_binary, the version query raises FileNotFoundError
    like a real missing executable. When captured_env is given, the env
    dict of every bash call is appended to it. With mock_stage_ssl the
    HTTPS-ensure stage is stubbed out so non-SSL tests do not exercise
    the certificate helpers; SSL tests pass False and set up their own.
    """

    calls: list[list[str]] = []
    started = False

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        nonlocal started
        if command[0] == "bash" and captured_env is not None:
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.append(env)
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
    if mock_stage_ssl:
        monkeypatch.setattr(xui, "_stage_ssl", lambda _cfg, _timeout: None)
    if mock_takeover:
        monkeypatch.setattr(
            xui, "_takeover_credentials", lambda _c, _t, _creds: (False, "")
        )
    if mock_settings:
        monkeypatch.setattr(
            "pyntara.xui.ensure_subscription_paths",
            lambda _cfg, _env, _timeout: (False, ""),
        )
    if mock_connection:
        monkeypatch.setattr(
            xui, "_stage_connection", lambda _cfg, _full_config, _timeout: None
        )
    return calls


def _panel_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    show_port: str = "35353",
    cert_value: str | None = None,
    mock_stage_ssl: bool = True,
    mock_takeover: bool = True,
    mock_settings: bool = True,
    mock_connection: bool = True,
) -> list[list[str]]:
    """Fake subprocess with a queryable panel; return the command calls.

    Answers `x-ui setting -show true` with the given port and `setting
    -getCert true` with the given certificate, so the port convergence
    and the SSL checks work through the real helpers. curl serves the
    release JSON and writes the fixture installer; bash runs it; the
    service reports enabled and active.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if command[0] == "curl":
            if "--output" in command:
                path = Path(command[command.index("--output") + 1])
                path.write_text("#!/bin/sh\necho fake installer\n", encoding="utf-8")
            return _FakeProc(0, _release_json())
        if command[0] == "bash":
            return _FakeProc(0)
        if command[0] == "ip":
            return _FakeProc(0)
        if command[0].endswith("x-ui"):
            if command[1:3] == ["setting", "-show"]:
                return _FakeProc(0, f"port: {show_port}\nwebBasePath: /xui/\n")
            if command[1:3] == ["setting", "-getCert"]:
                return _FakeProc(0, f"cert: {cert_value or ''}\n")
            if command[1:3] == ["setting", "-port"]:
                return _FakeProc(0)
            if command[1] == "-v":
                return _FakeProc(0, f"{TAG}\n")
        if command[0] == "systemctl":
            if command[1] == "is-enabled":
                return _FakeProc(0, "enabled\n")
            if command[1] == "is-active":
                return _FakeProc(0, "active\n")
            return _FakeProc(0)
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    if mock_stage_ssl:
        monkeypatch.setattr(xui, "_stage_ssl", lambda _cfg, _timeout: None)
    if mock_takeover:
        monkeypatch.setattr(
            xui, "_takeover_credentials", lambda _c, _t, _creds: (False, "")
        )
    if mock_settings:
        monkeypatch.setattr(
            "pyntara.xui.ensure_subscription_paths",
            lambda _cfg, _env, _timeout: (False, ""),
        )
    if mock_connection:
        monkeypatch.setattr(
            xui, "_stage_connection", lambda _cfg, _full_config, _timeout: None
        )
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
    assert result.changed is True  # stage 3 created inbound
    assert result.message == "already configured"
    expected_flags = curl_flags(
        ctx.config.engine.curl_timeout_seconds,
        ctx.config.engine.curl_retries,
        ctx.config.engine.curl_connect_timeout_seconds,
        ctx.config.engine.curl_retry_max_time_seconds,
        ctx.config.engine.curl_retry_delay_seconds,
    )
    release_calls = [
        call
        for call in calls
        if call[0] == "curl" and "releases/latest" in " ".join(call)
    ]
    assert release_calls
    assert all(flag in release_calls[0] for flag in expected_flags)
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


class TestStage3:
    """Tests for stage 3: universal server inbound creation."""

    def test_creates_inbound_on_first_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # No inbound exists on the configured port: stage 3 generates a
        # keypair and creates the inbound.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=False)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.changed is True  # stage 3 created inbound

    def test_skips_when_inbound_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # An inbound on the configured port already exists: stage 3 does
        # nothing.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.changed is False

    def test_reports_keygen_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Key generation fails: stage 3 returns a warning.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=False, keygen_ok=False)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.warnings is not None
        assert any("REALITY keypair" in w for w in result.warnings)

    def test_reports_create_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Inbound creation fails: stage 3 returns a warning.
        _stage2_fake(
            monkeypatch, tmp_path, inbound_exists=False, create_inbound_ok=False
        )
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.warnings is not None
        assert any("creation failed" in w for w in result.warnings)


class TestProquintCredentials:
    """Tests for the proquint credential env passed to the installer."""

    def test_credential_env_has_proquint_format(self, tmp_path: Path) -> None:
        # The generated credentials have the fixed proquint shapes:
        # username 10 letters, password 20 letters, webBasePath 23 chars
        # with three dash separators, panel port from config.
        config = make_config(task_data_root=tmp_path)
        cfg = config.three_x_ui_xray_setup
        env = xui._credential_env(cfg)
        proquint_letters = frozenset("bdfghjklmnprstvzaiou")
        assert env["XUI_PANEL_PORT"] == str(cfg.panel_port)
        assert len(env["XUI_USERNAME"]) == 10
        assert set(env["XUI_USERNAME"]) <= proquint_letters
        assert len(env["XUI_PASSWORD"]) == 20
        assert set(env["XUI_PASSWORD"]) <= proquint_letters
        assert len(env["XUI_WEB_BASE_PATH"]) == 23
        assert env["XUI_WEB_BASE_PATH"].count("-") == 3
        assert set(env["XUI_WEB_BASE_PATH"].replace("-", "")) <= proquint_letters

    def test_installer_receives_credential_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The installer runs with XUI_NONINTERACTIVE plus the proquint
        # credentials, the fixed panel port and the SSL mode in its
        # environment.
        envs: list[dict[str, str]] = []
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
            captured_env=envs,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert envs, "installer bash call did not record an env"
        bash_env = envs[0]
        assert bash_env["XUI_NONINTERACTIVE"] == "1"
        assert bash_env["XUI_PANEL_PORT"] == "35353"
        assert bash_env["XUI_SSL_MODE"] == "ip"
        assert len(bash_env["XUI_USERNAME"]) == 10
        assert len(bash_env["XUI_PASSWORD"]) == 20
        assert len(bash_env["XUI_WEB_BASE_PATH"]) == 23

    def test_installer_omits_ssl_mode_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # With ssl_enabled=False the installer env has no XUI_SSL_MODE,
        # so the panel stays HTTP.
        envs: list[dict[str, str]] = []
        _stage2_fake(monkeypatch, tmp_path)
        ctx = make_context(
            install_mode="server",
            force_tasks=frozenset({"three_x_ui_xray_setup"}),
            task_data_root=tmp_path,
            skip_apt_update=True,
            config=make_config(
                task_data_root=tmp_path,
                three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
                three_x_ui_install_result_env_path=(
                    tmp_path / "etc" / "x-ui" / "install-result.env"
                ),
                three_x_ui_ssl_enabled=False,
            ),
        )
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
            captured_env=envs,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert envs
        assert "XUI_SSL_MODE" not in envs[0]

    def test_panel_port_freed_before_installer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The task frees the panel port and the ACME port before running
        # the installer, passing the service unit name and the x-ui
        # process name.
        captured: list[tuple[int, str, str | None]] = []

        def fake_ensure_port_free(
            port: int,
            service_name: str,
            _timeout: float,
            **kwargs: object,
        ) -> None:
            captured.append(
                (port, service_name, cast(str | None, kwargs.get("service_process_name")))
            )

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        _stage2_fake(monkeypatch, tmp_path)
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
        assert captured == [
            (35353, "x-ui.service", "x-ui"),
            (80, "x-ui.service", "x-ui"),
        ]

    def test_ssl_disabled_frees_only_panel_port(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # With ssl_enabled=False the task frees only the panel port, not
        # the ACME port.
        captured: list[tuple[int, str, str | None]] = []

        def fake_ensure_port_free(
            port: int,
            service_name: str,
            _timeout: float,
            **kwargs: object,
        ) -> None:
            captured.append(
                (port, service_name, cast(str | None, kwargs.get("service_process_name")))
            )

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = make_context(
            install_mode="server",
            force_tasks=frozenset({"three_x_ui_xray_setup"}),
            task_data_root=tmp_path,
            skip_apt_update=True,
            config=make_config(
                task_data_root=tmp_path,
                three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
                three_x_ui_install_result_env_path=(
                    tmp_path / "etc" / "x-ui" / "install-result.env"
                ),
                three_x_ui_ssl_enabled=False,
            ),
        )
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
        assert captured == [(35353, "x-ui.service", "x-ui")]

    def test_port_free_failure_reports_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The panel port stays occupied after the free attempt: the task
        # reports an error and never runs the installer.
        def fake_ensure_port_free(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("still occupied")

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        ctx = _ctx(tmp_path)
        calls = _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=None,
            enabled=False,
            active=False,
        )
        result = xui.task(ctx)
        assert result.success is False
        assert "still occupied" in (result.error or "")
        assert not any(call[0] == "bash" for call in calls)

    def test_nat_skip_installs_self_signed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A machine behind NAT without a port-80 forward: the installer
        # runs with XUI_SSL_MODE=none, the ACME port is not freed, and
        # the HTTPS stage leaves the panel on a self-signed certificate
        # instead of plain HTTP.
        envs: list[dict[str, str]] = []
        captured: list[int] = []

        def fake_ensure_port_free(
            port: int, *_args: object, **_kwargs: object
        ) -> None:
            captured.append(port)

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _cfg, _timeout: False)
        monkeypatch.setattr(
            xui,
            "_stage_ssl",
            lambda _cfg, _timeout: TaskResult(
                success=True,
                changed=True,
                message="panel serves HTTPS with a self-signed certificate",
            ),
        )
        monkeypatch.setattr(
            xui, "_converge_panel_port", lambda _cfg, _timeout: (False, None)
        )
        monkeypatch.setattr(
            xui, "_sync_install_result_env", lambda _cfg, _timeout: False
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path, force=True)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
            captured_env=envs,
            mock_stage_ssl=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert envs[0]["XUI_SSL_MODE"] == "none"
        assert captured == [35353]
        assert "self-signed" in result.message
        assert not any("SSL skipped" in w for w in result.warnings or ())

    def test_nat_forward_attempts_ssl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A machine behind NAT with a confirmed port-80 forward: SSL is
        # attempted with XUI_SSL_MODE=ip and the ACME port is freed. The
        # HTTPS stage sees the installer-issued certificate and changes
        # nothing.
        envs: list[dict[str, str]] = []
        captured: list[int] = []

        def fake_ensure_port_free(
            port: int, *_args: object, **_kwargs: object
        ) -> None:
            captured.append(port)

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _cfg, _timeout: True)
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda _cfg, _timeout: "/root/cert/ip/fullchain.pem",
        )
        monkeypatch.setattr(
            xui, "_converge_panel_port", lambda _cfg, _timeout: (False, None)
        )
        monkeypatch.setattr(
            xui, "_sync_install_result_env", lambda _cfg, _timeout: False
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path, force=True)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
            captured_env=envs,
            mock_stage_ssl=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert envs[0]["XUI_SSL_MODE"] == "ip"
        assert captured == [35353, 80]

    def test_stage_ssl_warning_reaches_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The HTTPS stage returns a warning (a trusted certificate could
        # not be issued): the task result carries it, so the incomplete
        # configuration stays visible.
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _cfg, _timeout: True)
        monkeypatch.setattr(
            xui,
            "_stage_ssl",
            lambda _cfg, _timeout: TaskResult(
                success=True,
                changed=False,
                warnings=(
                    (
                        "trusted certificate could not be issued; panel "
                        "serves HTTPS with a self-signed certificate"
                    ),
                ),
            ),
        )
        monkeypatch.setattr(
            xui, "_converge_panel_port", lambda _cfg, _timeout: (False, None)
        )
        monkeypatch.setattr(
            xui, "_sync_install_result_env", lambda _cfg, _timeout: False
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path, force=True)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
            mock_stage_ssl=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert any("could not be issued" in w for w in result.warnings or ())

    def test_sync_runs_after_stage_ssl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The install-result.env sync runs after the HTTPS stage, so the
        # scheme in the file reflects the certificate the stage set.
        order: list[str] = []

        def fake_stage_ssl(_cfg: object, _timeout: float) -> None:
            order.append("stage_ssl")

        def fake_sync(_cfg: object, _timeout: float) -> bool:
            order.append("sync")
            return False

        monkeypatch.setattr(xui, "_stage_ssl", fake_stage_ssl)
        monkeypatch.setattr(xui, "_sync_install_result_env", fake_sync)
        monkeypatch.setattr(
            xui, "_converge_panel_port", lambda _cfg, _timeout: (False, None)
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_stage_ssl=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert order == ["stage_ssl", "sync"]


class TestPanelPortConvergence:
    """Tests for bringing the panel to the configured port."""

    def _cfg(self, tmp_path: Path) -> ThreeXuiXraySetupConfig:
        return make_config(
            task_data_root=tmp_path,
            three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
            three_x_ui_install_result_env_path=(
                tmp_path / "etc" / "x-ui" / "install-result.env"
            ),
        ).three_x_ui_xray_setup

    def test_converge_panel_port_migrates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel is on a different port: the target port is freed, the
        # new port is set and the panel restarts.
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            if command[1:3] == ["setting", "-show"]:
                return _FakeProc(0, "port: 35905\nwebBasePath: /xui/\n")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        monkeypatch.setattr(xui, "ensure_port_free", lambda *a, **k: None)
        cfg = self._cfg(tmp_path)
        changed, message = xui._converge_panel_port(cfg, 30)
        assert changed is True
        assert message == "panel port moved to 35353"
        assert any(c[1:4] == ["setting", "-port", "35353"] for c in calls)
        assert ["systemctl", "restart", "x-ui.service"] in calls

    def test_converge_panel_port_noop_when_already_correct(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel already listens on the configured port: nothing changes.
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            if command[1:3] == ["setting", "-show"]:
                return _FakeProc(0, "port: 35353\n")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        cfg = self._cfg(tmp_path)
        changed, message = xui._converge_panel_port(cfg, 30)
        assert changed is False
        assert message is None
        assert not any(c[1:3] == ["setting", "-port"] for c in calls)

    def test_converge_panel_port_raises_when_occupied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The target port stays occupied: the migration raises.
        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            if command[1:3] == ["setting", "-show"]:
                return _FakeProc(0, "port: 35905\n")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        monkeypatch.setattr(
            xui,
            "ensure_port_free",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("still occupied")),
        )
        cfg = self._cfg(tmp_path)
        with pytest.raises(RuntimeError):
            xui._converge_panel_port(cfg, 30)

    def test_converges_panel_port_after_install(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The installer left the panel on an old port: the task brings it
        # to the configured port and updates install-result.env.
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path, force=True)
        calls = _panel_fake(monkeypatch, show_port="35905")
        result = xui.task(ctx)
        assert result.success is True
        assert any(c[1:4] == ["setting", "-port", "35353"] for c in calls)
        assert ["systemctl", "restart", "x-ui.service"] in calls
        env_text = (tmp_path / "etc" / "x-ui" / "install-result.env").read_text(
            encoding="utf-8"
        )
        assert "XUI_PANEL_PORT=35353" in env_text

    def test_converges_panel_port_on_rerun(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A rerun finds the panel on an old port and migrates it to the
        # configured one, reporting a change.
        monkeypatch.setattr(xui, "_stage_ssl", lambda _cfg, _timeout: None)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path)
        calls = _panel_fake(monkeypatch, show_port="35905")
        result = xui.task(ctx)
        assert result.success is True
        assert result.changed is True
        assert any(c[1:4] == ["setting", "-port", "35353"] for c in calls)
        assert ["systemctl", "restart", "x-ui.service"] in calls

    def test_sync_install_result_env_updates_port_and_scheme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The file carries a stale port and an http access url while the
        # panel serves https: both are rewritten to match reality.
        env_path = tmp_path / "etc" / "x-ui" / "install-result.env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(
            "XUI_USERNAME=admin\nXUI_PASSWORD=pass\nXUI_PANEL_PORT=3579\n"
            "XUI_WEB_BASE_PATH=/xui\n"
            "XUI_ACCESS_URL=http://203.0.113.5:3579/xui\n"
            "XUI_API_TOKEN=tok\nXUI_DB_TYPE=sqlite\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda _c, _t: "https")
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_install_result_env_path=env_path,
        )
        cfg = config.three_x_ui_xray_setup
        assert xui._sync_install_result_env(cfg, 30) is True
        text = env_path.read_text(encoding="utf-8")
        assert "XUI_PANEL_PORT=35353" in text
        assert "XUI_ACCESS_URL=https://203.0.113.5:35353/xui" in text

    def test_sync_install_result_env_noop_when_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The file already carries the real port and scheme: no rewrite.
        env_path = tmp_path / "etc" / "x-ui" / "install-result.env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(
            "XUI_USERNAME=admin\nXUI_PASSWORD=pass\nXUI_PANEL_PORT=35353\n"
            "XUI_WEB_BASE_PATH=/xui\n"
            "XUI_ACCESS_URL=http://203.0.113.5:35353/xui\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda _c, _t: "http")
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_install_result_env_path=env_path,
        )
        cfg = config.three_x_ui_xray_setup
        assert xui._sync_install_result_env(cfg, 30) is False

    def test_wait_panel_http_returns_when_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel answers on the first poll: the wait returns True.
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command",
            lambda *a, **k: _FakeProc(0, ""),
        )
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda _c, _t: "http")
        assert xui._wait_panel_http(self._cfg(tmp_path), 30) is True

    def test_wait_panel_http_retries_then_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel never answers: the wait retries and reports False.
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command",
            lambda *a, **k: _FakeProc(7, ""),
        )
        monkeypatch.setattr(xui.time, "sleep", lambda _s: None)
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda _c, _t: "http")
        assert xui._wait_panel_http(self._cfg(tmp_path), 30) is False

    def test_wait_panel_http_uses_the_configured_attempts_and_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The probe timeout, the attempts and the pause come from the
        # config, so a slow link is not reported as an unreachable panel.
        commands: list[list[str]] = []
        sleeps: list[int] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            commands.append(command)
            return _FakeProc(7, "")

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        monkeypatch.setattr(xui.time, "sleep", lambda seconds: sleeps.append(seconds))
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda _c, _t: "http")
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_install_result_env_path=tmp_path / "missing.env",
            three_x_ui_probe_timeout_seconds=90,
            three_x_ui_start_check_attempts=3,
            three_x_ui_start_check_retry_delay_seconds=2,
        )
        cfg = config.three_x_ui_xray_setup
        assert xui._wait_panel_http(cfg, 30) is False
        assert len(commands) == 3
        assert sleeps == [2, 2, 2]
        assert commands[0][commands[0].index("--max-time") + 1] == "90"


class TestSslReachability:
    """Tests for deciding whether the HTTP-01 challenge can be served."""

    def test_is_private_ipv4_ranges(self) -> None:
        assert xui._is_private_ipv4("10.0.0.1") is True
        assert xui._is_private_ipv4("172.16.0.1") is True
        assert xui._is_private_ipv4("172.31.255.255") is True
        assert xui._is_private_ipv4("172.32.0.1") is False
        assert xui._is_private_ipv4("192.168.1.1") is True
        assert xui._is_private_ipv4("203.0.113.5") is False

    def test_local_ipv4_parses_first_inet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command",
            lambda *a, **k: _FakeProc(
                0,
                "3: wlp86s0 inet 192.168.187.146/24 brd 192.168.187.255 "
                "scope global dynamic\n",
            ),
        )
        assert xui._local_ipv4(30) == "192.168.187.146"

    def test_local_ipv4_none_on_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command",
            lambda *a, **k: _FakeProc(0, ""),
        )
        assert xui._local_ipv4(30) is None

    def _cfg(self) -> ThreeXuiXraySetupConfig:
        # A default three_x_ui config; the reachability helpers only read
        # the ACME port and the echo-service list, which the tests mock.
        return make_config().three_x_ui_xray_setup

    def test_ssl_reachable_public_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A public address on the interface allows the attempt directly.
        monkeypatch.setattr(xui, "_local_ipv4", lambda _t: "203.0.113.5")
        monkeypatch.setattr(xui, "_probe_port_80_forward", lambda _cfg, _t: False)
        assert xui._ssl_reachable(self._cfg(), 30) is True

    def test_ssl_reachable_private_with_forward(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Behind NAT with a confirmed port-80 forward: attempt SSL.
        monkeypatch.setattr(xui, "_local_ipv4", lambda _t: "192.168.1.10")
        monkeypatch.setattr(xui, "_probe_port_80_forward", lambda _cfg, _t: True)
        assert xui._ssl_reachable(self._cfg(), 30) is True

    def test_ssl_reachable_private_without_forward(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Behind NAT without a forward: skip SSL.
        monkeypatch.setattr(xui, "_local_ipv4", lambda _t: "192.168.1.10")
        monkeypatch.setattr(xui, "_probe_port_80_forward", lambda _cfg, _t: False)
        assert xui._ssl_reachable(self._cfg(), 30) is False

    def test_ssl_reachable_unknown_local_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unknown local address: the attempt is allowed, never skipped.
        monkeypatch.setattr(xui, "_local_ipv4", lambda _t: None)
        monkeypatch.setattr(xui, "_probe_port_80_forward", lambda _cfg, _t: False)
        assert xui._ssl_reachable(self._cfg(), 30) is True

    def test_probe_port_80_forward_confirmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The temporary listener answers a connection through the public
        # address: the forward is confirmed.
        fake_proc = Mock()
        monkeypatch.setattr(
            xui, "_detect_server_ip", lambda _cfg, _t: "203.0.113.5"
        )
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.subprocess.Popen",
            lambda *a, **k: fake_proc,
        )
        monkeypatch.setattr(xui.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command",
            lambda *a, **k: _FakeProc(0, "ok"),
        )
        assert xui._probe_port_80_forward(self._cfg(), 30) is True
        fake_proc.terminate.assert_called_once()

    def test_probe_port_80_forward_not_confirmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The connection fails (no forward): not confirmed.
        fake_proc = Mock()
        monkeypatch.setattr(
            xui, "_detect_server_ip", lambda _cfg, _t: "203.0.113.5"
        )
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.subprocess.Popen",
            lambda *a, **k: fake_proc,
        )
        monkeypatch.setattr(xui.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command",
            lambda *a, **k: _FakeProc(7, ""),
        )
        assert xui._probe_port_80_forward(self._cfg(), 30) is False
        fake_proc.terminate.assert_called_once()

    def test_probe_port_80_forward_needs_public_ip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No public address: the probe cannot confirm a forward.
        monkeypatch.setattr(xui, "_detect_server_ip", lambda _cfg, _t: None)
        assert xui._probe_port_80_forward(self._cfg(), 30) is False

    def test_probe_port_80_forward_uses_the_configured_timeouts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The probe timeouts and the listener pause come from the config:
        # a few hardcoded seconds report a working service as unreachable
        # on a slow link.
        sleeps: list[int] = []
        commands: list[list[str]] = []
        fake_proc = Mock()
        monkeypatch.setattr(
            xui, "_detect_server_ip", lambda _cfg, _t: "203.0.113.5"
        )
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.subprocess.Popen",
            lambda *a, **k: fake_proc,
        )
        monkeypatch.setattr(xui.time, "sleep", lambda seconds: sleeps.append(seconds))

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            commands.append(command)
            return _FakeProc(0, "ok")

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        cfg = make_config(
            three_x_ui_probe_timeout_seconds=90,
            three_x_ui_probe_listener_start_seconds=4,
        ).three_x_ui_xray_setup
        assert xui._probe_port_80_forward(cfg, 30) is True
        assert sleeps == [4]
        command = commands[0]
        assert command[command.index("--connect-timeout") + 1] == "90"
        assert command[command.index("--max-time") + 1] == "90"
        fake_proc.wait.assert_called_once_with(timeout=90)


class TestStageSsl:
    """Tests for stage 4: ensuring the panel serves HTTPS."""

    def _cfg(
        self, tmp_path: Path, *, ssl_enabled: bool = True
    ) -> ThreeXuiXraySetupConfig:
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
            three_x_ui_ssl_enabled=ssl_enabled,
            three_x_ui_self_signed_cert_dir=tmp_path / "selfsigned",
        )
        return config.three_x_ui_xray_setup

    def test_stage_ssl_skipped_when_disabled(self, tmp_path: Path) -> None:
        # ssl_enabled=False disables the whole stage.
        assert xui._stage_ssl(self._cfg(tmp_path, ssl_enabled=False), 30) is None

    def test_stage_ssl_does_nothing_when_cert_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A panel that already carries a trusted or foreign certificate
        # is left alone.
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda _cfg, _timeout: "/root/cert/ip/fullchain.pem",
        )
        assert xui._stage_ssl(self._cfg(tmp_path), 30) is None

    def test_stage_ssl_installs_self_signed_when_no_ip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No public address can be detected, so a trusted certificate is
        # impossible: the stage installs a self-signed one.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(xui, "_detect_server_ip", lambda _cfg, _timeout: None)
        monkeypatch.setattr(
            xui,
            "_ensure_self_signed_cert",
            lambda _cfg, _timeout: (True, "self-signed certificate configured"),
        )
        result = xui._stage_ssl(self._cfg(tmp_path), 30)
        assert result is not None
        assert result.changed is True
        assert "self-signed" in (result.message or "")

    def test_stage_ssl_installs_self_signed_when_not_reachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The machine is behind NAT without a port-80 forward: the stage
        # installs a self-signed certificate instead of leaving HTTP.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(
            xui, "_detect_server_ip", lambda _cfg, _timeout: "203.0.113.5"
        )
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _cfg, _timeout: False)
        monkeypatch.setattr(
            xui,
            "_ensure_self_signed_cert",
            lambda _cfg, _timeout: (True, "self-signed certificate configured"),
        )
        result = xui._stage_ssl(self._cfg(tmp_path), 30)
        assert result is not None
        assert result.changed is True
        assert "self-signed" in (result.message or "")

    def test_stage_ssl_keeps_self_signed_when_unreachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel already serves our self-signed certificate and port
        # 80 is still unreachable: nothing changes.
        cfg = self._cfg(tmp_path)
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda _cfg, _timeout: str(cfg.self_signed_cert_fullchain),
        )
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _cfg, _timeout: False)
        assert xui._stage_ssl(cfg, 30) is None

    def test_stage_ssl_upgrades_self_signed_when_reachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel serves our self-signed certificate and port 80 has
        # become reachable: the stage replaces it with a trusted one.
        cfg = self._cfg(tmp_path)
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda _cfg, _timeout: str(cfg.self_signed_cert_fullchain),
        )
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _cfg, _timeout: True)
        monkeypatch.setattr(
            xui, "_detect_server_ip", lambda _cfg, _timeout: "203.0.113.5"
        )
        monkeypatch.setattr(
            xui,
            "_issue_ip_certificate",
            lambda _cfg, ip, _timeout: (True, "certificate issued"),
        )
        monkeypatch.setattr(xui, "ensure_port_free", lambda *a, **k: None)
        result = xui._stage_ssl(cfg, 30)
        assert result is not None
        assert result.changed is True
        assert result.message == "SSL certificate configured"

    def test_stage_ssl_issues_cert_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No certificate and port 80 reachable: the stage detects the IP,
        # frees the ACME port and issues the certificate.
        seen: dict[str, object] = {}

        def fake_issue(_cfg: object, ip: str, _timeout: float) -> tuple[bool, str]:
            seen["ip"] = ip
            return True, "certificate issued"

        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(
            xui, "_detect_server_ip", lambda _cfg, _timeout: "203.0.113.5"
        )
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _cfg, _timeout: True)
        monkeypatch.setattr(xui, "_issue_ip_certificate", fake_issue)
        monkeypatch.setattr(xui, "ensure_port_free", lambda *a, **k: None)
        result = xui._stage_ssl(self._cfg(tmp_path), 30)
        assert result is not None
        assert result.changed is True
        assert seen["ip"] == "203.0.113.5"

    def test_stage_ssl_warns_on_acme_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # acme.sh fails to issue the certificate: the stage warns.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(
            xui, "_detect_server_ip", lambda _cfg, _timeout: "203.0.113.5"
        )
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _cfg, _timeout: True)
        monkeypatch.setattr(
            xui,
            "_issue_ip_certificate",
            lambda _cfg, ip, _timeout: (False, "port 80 unreachable"),
        )
        monkeypatch.setattr(xui, "ensure_port_free", lambda *a, **k: None)
        result = xui._stage_ssl(self._cfg(tmp_path), 30)
        assert result is not None
        assert result.changed is False
        assert any("SSL certificate setup failed" in w for w in result.warnings or ())

    def test_stage_ssl_warns_when_self_signed_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Neither a trusted nor a self-signed certificate can be set up
        # (openssl unavailable): the stage warns that the panel stays on
        # HTTP.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(xui, "_detect_server_ip", lambda _cfg, _timeout: None)
        monkeypatch.setattr(
            xui,
            "_ensure_self_signed_cert",
            lambda _cfg, _timeout: (
                False,
                "openssl unavailable: cannot generate a self-signed certificate",
            ),
        )
        result = xui._stage_ssl(self._cfg(tmp_path), 30)
        assert result is not None
        assert result.changed is False
        assert any("panel serves HTTP" in w for w in result.warnings or ())

    def test_issue_ip_certificate_runs_acme_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The acme.sh command sequence mirrors the installer: create the
        # certificate directory, issue, installcert and point the panel
        # at the files.
        calls: list[list[str]] = []
        cert_dir = tmp_path / "cert"
        monkeypatch.setattr(xui, "_ensure_acme", lambda _timeout: True)
        monkeypatch.setattr(xui, "_acme_path", lambda: Path("/tmp/acme.sh"))
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_cert_dir=cert_dir,
            three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
        )
        cfg = config.three_x_ui_xray_setup

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            if command[1] == "--installcert":
                cfg.cert_fullchain.write_text("fullchain", encoding="utf-8")
                cfg.cert_privkey.write_text("privkey", encoding="utf-8")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        ok, message = xui._issue_ip_certificate(cfg, "203.0.113.5", 30)
        assert ok is True
        assert cert_dir.is_dir()
        assert any(command[1] == "--issue" and "203.0.113.5" in command for command in calls)
        assert any(command[1] == "--installcert" for command in calls)
        assert any(
            command[0] == str(cfg.install_dir / "x-ui") and command[1] == "cert"
            for command in calls
        )
        # The panel must restart after the cert paths are set to serve
        # TLS with the new certificate.
        assert ["systemctl", "restart", cfg.service_unit_name] in calls
        assert cfg.cert_privkey.stat().st_mode & 0o777 == 0o600
        assert cfg.cert_fullchain.stat().st_mode & 0o777 == 0o644
        assert message == "certificate issued"

    def test_issue_ip_certificate_warns_on_step_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failed acme.sh step reports a failure message.
        monkeypatch.setattr(xui, "_ensure_acme", lambda _timeout: True)
        monkeypatch.setattr(xui, "_acme_path", lambda: Path("/tmp/acme.sh"))
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_cert_dir=tmp_path / "cert",
        )

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            if command[1] == "--issue":
                return _FakeProc(1, "error")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        ok, message = xui._issue_ip_certificate(
            config.three_x_ui_xray_setup, "203.0.113.5", 30
        )
        assert ok is False
        assert "acme.sh step failed" in message


class TestSelfSignedCert:
    """Tests for the self-signed certificate helper."""

    def _cfg(self, tmp_path: Path) -> ThreeXuiXraySetupConfig:
        return make_config(
            task_data_root=tmp_path,
            three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
            three_x_ui_self_signed_cert_dir=tmp_path / "selfsigned",
        ).three_x_ui_xray_setup

    def test_installs_when_no_cert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No certificate configured: the helper generates the files with
        # openssl, points the panel at them and restarts it.
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            if command[0] == "openssl" and command[1] == "req":
                keyout = command[command.index("-keyout") + 1]
                out = command[command.index("-out") + 1]
                Path(keyout).parent.mkdir(parents=True, exist_ok=True)
                Path(keyout).write_text("key", encoding="utf-8")
                Path(out).write_text("cert", encoding="utf-8")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.package_is_installed",
            lambda _p, _t: True,
        )
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda _cfg, _t: None)
        cfg = self._cfg(tmp_path)
        ok, message = xui._ensure_self_signed_cert(cfg, 30)
        assert ok is True
        assert message == "self-signed certificate configured"
        assert any(
            command[0] == "openssl" and command[1] == "req" for command in calls
        )
        assert any(
            command[0] == str(cfg.install_dir / "x-ui")
            and command[1] == "cert"
            and str(cfg.self_signed_cert_fullchain) in command
            for command in calls
        )
        assert ["systemctl", "restart", cfg.service_unit_name] in calls
        assert cfg.self_signed_cert_privkey.stat().st_mode & 0o777 == 0o600
        assert cfg.self_signed_cert_fullchain.stat().st_mode & 0o777 == 0o644

    def test_noop_when_already_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel already points at our valid self-signed files: no
        # regeneration, no panel change.
        cfg = self._cfg(tmp_path)
        cfg.self_signed_cert_dir.mkdir(parents=True, exist_ok=True)
        cfg.self_signed_cert_fullchain.write_text("cert", encoding="utf-8")
        cfg.self_signed_cert_privkey.write_text("key", encoding="utf-8")
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda _cfg, _t: str(cfg.self_signed_cert_fullchain),
        )
        ok, message = xui._ensure_self_signed_cert(cfg, 30)
        assert ok is False
        assert message == ""
        assert not any(command[:2] == ["openssl", "req"] for command in calls)
        assert not any(
            command[:2] == [str(cfg.install_dir / "x-ui"), "cert"]
            for command in calls
        )
        assert ["systemctl", "restart", cfg.service_unit_name] not in calls

    def test_does_not_touch_foreign_cert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel carries a certificate we do not own: leave it alone.
        cfg = self._cfg(tmp_path)
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda _cfg, _t: "/root/cert/ip/fullchain.pem",
        )
        ok, message = xui._ensure_self_signed_cert(cfg, 30)
        assert ok is False
        assert message == ""
        assert calls == []

    def test_warns_when_openssl_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # openssl cannot be installed: the helper reports the failure so
        # the caller falls back to the HTTP warning.
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.package_is_installed",
            lambda _p, _t: False,
        )
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.install_package_once",
            lambda _p, _t: (False, "apt failed"),
        )
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda _cfg, _t: None)
        cfg = self._cfg(tmp_path)
        ok, message = xui._ensure_self_signed_cert(cfg, 30)
        assert ok is False
        assert "openssl unavailable" in message

    def test_regenerates_expired_cert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel points at our files but the certificate has expired:
        # the helper regenerates and re-applies it.
        cfg = self._cfg(tmp_path)
        cfg.self_signed_cert_dir.mkdir(parents=True, exist_ok=True)
        cfg.self_signed_cert_fullchain.write_text("old-cert", encoding="utf-8")
        cfg.self_signed_cert_privkey.write_text("old-key", encoding="utf-8")
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            if command[0] == "openssl" and command[1] == "x509":
                return _FakeProc(1, "")  # -checkend reports expired
            if command[0] == "openssl" and command[1] == "req":
                keyout = command[command.index("-keyout") + 1]
                out = command[command.index("-out") + 1]
                Path(keyout).write_text("new-key", encoding="utf-8")
                Path(out).write_text("new-cert", encoding="utf-8")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.package_is_installed",
            lambda _p, _t: True,
        )
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda _cfg, _t: str(cfg.self_signed_cert_fullchain),
        )
        ok, _ = xui._ensure_self_signed_cert(cfg, 30)
        assert ok is True
        assert any(
            command[0] == "openssl" and command[1] == "req" for command in calls
        )
        assert cfg.self_signed_cert_fullchain.read_text(encoding="utf-8") == "new-cert"

    def test_rerun_invokes_stage_ssl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # On a rerun (target state reached, installer skipped) the task
        # runs stage 4.
        called: list[bool] = []

        def _fake_stage_ssl(*args: object, **kwargs: object) -> None:
            called.append(True)

        monkeypatch.setattr(xui, "_stage_ssl", _fake_stage_ssl)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_stage_ssl=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert called == [True]

    def test_rerun_with_ssl_issue_reports_changed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A rerun that issues the missing certificate reports changed.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(
            xui, "_detect_server_ip", lambda _cfg, _timeout: "203.0.113.5"
        )
        monkeypatch.setattr(
            xui,
            "_issue_ip_certificate",
            lambda _cfg, ip, _timeout: (True, "certificate issued"),
        )
        monkeypatch.setattr(xui, "ensure_port_free", lambda *a, **k: None)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_stage_ssl=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.changed is True

    def test_panel_env_adds_https_scheme_when_tls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The install-result.env pairs carry the panel URL scheme, so the
        # API client talks TLS to a panel that serves a certificate.
        env_path = tmp_path / "etc" / "x-ui" / "install-result.env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(
            "XUI_USERNAME=admin\nXUI_PASSWORD=pass\nXUI_PANEL_PORT=35353\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda _cfg, _timeout: "https")
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_install_result_env_path=env_path,
        )
        env = xui._panel_env(config.three_x_ui_xray_setup, 30)
        assert env["XUI_SCHEME"] == "https"
        assert env["XUI_PANEL_PORT"] == "35353"

    def test_stage2_vault_url_uses_https_when_tls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A panel that serves TLS is stored in the vault with an https
        # base url.
        env_path = tmp_path / "etc" / "x-ui" / "install-result.env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(
            "XUI_USERNAME=admin\nXUI_PASSWORD=pass\nXUI_PANEL_PORT=35353\n"
            "XUI_WEB_BASE_PATH=/xui\nXUI_API_TOKEN=tok\nXUI_DB_TYPE=sqlite\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("pyntara.xui.login_and_verify", lambda _c, _e, _t: True)
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda _c, _t: "https")
        fake_kp = Mock()
        fake_kp.find_entries.return_value = None
        fake_kp.root_group = Mock()
        fake_kp.add_entry = Mock()
        fake_kp.save = Mock()
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda _c: fake_kp)
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_install_result_env_path=env_path,
        )
        result = xui._stage2(config.three_x_ui_xray_setup, config, 30)
        assert result is None
        url = fake_kp.add_entry.call_args.kwargs["url"]
        assert url.startswith("https://")


class TestRewriteEnv:
    """Tests for the shared ordered env-file rewrite helper."""

    def test_updates_values_preserving_order(self, tmp_path: Path) -> None:
        path = tmp_path / "env"
        path.write_text("A=1\nB=2\nC=3\n", encoding="utf-8")
        assert xui._rewrite_env(path, {"B": "9", "D": "4"}) is True
        assert path.read_text(encoding="utf-8") == "A=1\nB=9\nC=3\nD=4\n"

    def test_noop_when_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "env"
        path.write_text("A=1\n", encoding="utf-8")
        assert xui._rewrite_env(path, {"A": "1"}) is False
        assert path.read_text(encoding="utf-8") == "A=1\n"

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        assert xui._rewrite_env(tmp_path / "nope", {"A": "1"}) is False


class TestTakeoverCredentials:
    """Tests for the force credential and webBasePath takeover."""

    def _cfg(self, tmp_path: Path) -> ThreeXuiXraySetupConfig:
        return make_config(
            task_data_root=tmp_path,
            three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
            three_x_ui_install_result_env_path=(
                tmp_path / "etc" / "x-ui" / "install-result.env"
            ),
        ).three_x_ui_xray_setup

    def test_applies_credentials_and_rewrites_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The fresh values are set through x-ui setting, the env file is
        # rewritten through the shared helper and the panel restarts.
        env_path = tmp_path / "etc" / "x-ui" / "install-result.env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(
            "XUI_USERNAME=old\nXUI_PASSWORD=old\n"
            "XUI_WEB_BASE_PATH=oldpath\nXUI_PANEL_PORT=35353\n",
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        cfg = self._cfg(tmp_path)
        creds = {
            "XUI_USERNAME": "newuser",
            "XUI_PASSWORD": "newpass",
            "XUI_WEB_BASE_PATH": "new-path-here",
        }
        ok, message = xui._takeover_credentials(cfg, 30, creds)
        assert ok is True
        assert "new-path-here" in message
        assert any(
            command[0] == str(cfg.install_dir / "x-ui")
            and command[1:3] == ["setting", "-username"]
            and "newuser" in command
            and "newpass" in command
            and "new-path-here" in command
            for command in calls
        )
        assert ["systemctl", "restart", cfg.service_unit_name] in calls
        text = env_path.read_text(encoding="utf-8")
        assert "XUI_USERNAME=newuser" in text
        assert "XUI_PASSWORD=newpass" in text
        assert "XUI_WEB_BASE_PATH=new-path-here" in text
        assert "XUI_PANEL_PORT=35353" in text

    def test_setting_failure_reports_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            if command[1:3] == ["setting", "-username"]:
                raise subprocess.CalledProcessError(1, command)
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        cfg = self._cfg(tmp_path)
        ok, message = xui._takeover_credentials(
            cfg,
            30,
            {"XUI_USERNAME": "u", "XUI_PASSWORD": "p", "XUI_WEB_BASE_PATH": "w"},
        )
        assert ok is False
        assert "cannot set panel credentials" in message

    def test_restart_failure_reports_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            if command[0] == "systemctl":
                raise subprocess.CalledProcessError(1, command)
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        cfg = self._cfg(tmp_path)
        ok, message = xui._takeover_credentials(
            cfg,
            30,
            {"XUI_USERNAME": "u", "XUI_PASSWORD": "p", "XUI_WEB_BASE_PATH": "w"},
        )
        assert ok is False
        assert "cannot restart" in message


class TestForceTakeoverWiring:
    """Tests for the force takeover in the task flow."""

    def _takeover_fake(
        self, monkeypatch: pytest.MonkeyPatch, seen: list[bool]
    ) -> None:
        def fake_takeover(
            _cfg: object, _timeout: float, _creds: dict[str, str]
        ) -> tuple[bool, str]:
            seen.append(True)
            return (
                True,
                (
                    "panel credentials and webBasePath set to fresh proquint "
                    "values (new-path)"
                ),
            )

        monkeypatch.setattr(xui, "_takeover_credentials", fake_takeover)

    def test_force_takes_over_existing_panel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Force on an existing panel applies fresh credentials through the
        # takeover helper and reports it in the done message.
        seen: list[bool] = []
        self._takeover_fake(monkeypatch, seen)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path, force=True)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_takeover=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert seen == [True]
        assert (
            "panel credentials and webBasePath set to fresh proquint values"
            in result.message
        )

    def test_force_fresh_install_skips_takeover(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Force on a fresh install: the installer already applied our env
        # values, so the takeover is not needed.
        seen: list[bool] = []
        self._takeover_fake(monkeypatch, seen)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path, force=True)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
            mock_takeover=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert seen == []

    def test_normal_run_skips_takeover(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A normal rerun never touches credentials or webBasePath.
        seen: list[bool] = []
        self._takeover_fake(monkeypatch, seen)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_takeover=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert seen == []


class TestPanelSettingsStage:
    """Tests for applying the panel subscription paths."""

    def test_applies_subscription_paths(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The panel kept the default paths: the task writes the configured
        # ones and reports the change in its message.
        _stage2_fake(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "pyntara.xui.ensure_subscription_paths",
            lambda _cfg, _env, _timeout: (
                True,
                "subscription paths set to /s/, /j/, /c/",
            ),
        )
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_settings=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.changed is True
        assert "/s/" in (result.message or "")

    def test_reports_warning_when_settings_unreachable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The settings API is unreachable: the task completes with a
        # warning and the panel keeps its default paths.
        _stage2_fake(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "pyntara.xui.ensure_subscription_paths",
            lambda _cfg, _env, _timeout: (False, "cannot read panel settings"),
        )
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_settings=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert any("subscription paths" in w for w in result.warnings or ())


class TestInboundSecurityStage:
    """Tests for storing the REALITY public key on an existing inbound."""

    def _inbound(self) -> dict[str, object]:
        return {
            "id": 1,
            "port": 443,
            "protocol": "vless",
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "dest": "www.google.com:443",
                    "privateKey": "priv123",
                    "shortIds": ["6ba85179e30d4fc2"],
                },
            },
        }

    def test_stores_the_panel_key_pair(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # An inbound created by an older install has no public key: the
        # task takes the pair from the panel and writes it back.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True)
        inbound = self._inbound()
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda _c, _e, _p, _t: inbound
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound",
            lambda _c, _e, _ib, _t: (True, "inbound updated"),
        )
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.changed is True
        reality = inbound["streamSettings"]["realitySettings"]  # type: ignore[index]
        assert reality["privateKey"] == "priv123"
        assert reality["settings"] == {
            "publicKey": "pub123",
            "fingerprint": "chrome",
        }

    def test_reports_warning_when_the_panel_has_no_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The panel does not answer with a key pair: the task completes
        # with a warning and the inbound keeps its state.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True, keygen_ok=False)
        inbound = self._inbound()
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda _c, _e, _p, _t: inbound
        )
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert any("key pair" in w for w in result.warnings or ())


class TestConnectionStage:
    """Tests for the client and vault connection profile stage."""

    def _inbound(self) -> dict[str, object]:
        return {
            "id": 2,
            "port": 443,
            "shareAddr": "203.0.113.5",
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "dest": "www.google.com:443",
                    "privateKey": "priv123",
                    "shortIds": ["6ba85179e30d4fc2"],
                    "settings": {"publicKey": "pub123", "fingerprint": "chrome"},
                },
            },
        }

    def test_creates_the_client_and_stores_the_profile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # No client yet and no vault entry: the task creates the client and
        # writes the profile with the share link into the vault.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True)
        inbound = self._inbound()
        fake_kp = Mock()
        fake_kp.root_group = Mock()
        fake_kp.find_entries.return_value = None
        fake_kp.add_entry = Mock()
        fake_kp.save = Mock()
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda _c, _e, _p, _t: inbound
        )
        monkeypatch.setattr("pyntara.xui.find_client", lambda _c, _e, _m, _t: None)
        monkeypatch.setattr(
            "pyntara.xui.create_client",
            lambda _c, _e, _i, _j, _m, _s, _t: (True, "client created"),
        )
        monkeypatch.setattr(
            "pyntara.xui.client_links",
            lambda _c, _e, _m, _t: ["vless://x@203.0.113.5:443"],
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound", lambda _c, _e, _i, _t: (True, "updated")
        )
        monkeypatch.setattr(
            xui, "_server_share_address", lambda _c, _f, _i, _t: "203.0.113.5"
        )
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda _cfg: fake_kp)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_connection=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.changed is True
        # Stage 2 writes the panel credentials entry first, then the
        # connection stage writes the profile entry.
        titles = [call.args[1] for call in fake_kp.add_entry.call_args_list]
        assert titles == ["three_x_ui_credentials", "xray_connection"]

    def test_reports_warning_when_the_vault_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The runtime vault is unavailable: the client step still runs and
        # the task completes with a warning instead of failing.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True, vault_ok=False)
        inbound = self._inbound()
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda _c, _e, _p, _t: inbound
        )
        monkeypatch.setattr(
            "pyntara.xui.find_client", lambda _c, _e, _m, _t: {"email": "a-b"}
        )
        monkeypatch.setattr(
            "pyntara.xui.client_links",
            lambda _c, _e, _m, _t: ["vless://x@203.0.113.5:443"],
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound", lambda _c, _e, _i, _t: (True, "updated")
        )
        monkeypatch.setattr(
            xui, "_server_share_address", lambda _c, _f, _i, _t: "203.0.113.5"
        )
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda _cfg: None)
        ctx = _ctx(tmp_path)
        _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=TAG,
            enabled=True,
            active=True,
            mock_connection=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert any("vault unavailable" in w for w in result.warnings or ())


class TestDetectServerIp:
    """Tests for the public IPv4 lookup used by the SSL stage."""

    def test_passes_the_config_values_to_the_shared_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The task must not carry its own echo logic: it hands the
        # configured services and timeouts to the shared helper.
        calls: list[tuple[tuple[str, ...], int, float]] = []

        def fake_fetch(
            services: tuple[str, ...], query_timeout: int, command_timeout: float
        ) -> object:
            calls.append((services, query_timeout, command_timeout))
            return _addresses(ipv4=("203.0.113.7",))

        monkeypatch.setattr(xui, "fetch_public_addresses", fake_fetch)
        cfg = make_config(
            three_x_ui_server_ip_timeout_seconds=77,
            three_x_ui_server_ip_services=("https://api4.ipify.org",),
        ).three_x_ui_xray_setup
        assert xui._detect_server_ip(cfg, 30.0) == "203.0.113.7"
        assert calls == [(("https://api4.ipify.org",), 77, 30.0)]

    def test_returns_none_when_only_ipv6_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A Let's Encrypt IP certificate needs IPv4: an IPv6-only machine
        # reports no IPv4 address and keeps its self-signed certificate.
        monkeypatch.setattr(
            xui,
            "fetch_public_addresses",
            lambda _services, _query_timeout, _command_timeout: _addresses(
                ipv6=("2001:db8::1",)
            ),
        )
        cfg = make_config().three_x_ui_xray_setup
        assert xui._detect_server_ip(cfg, 30.0) is None


class TestServerShareAddress:
    """Tests for the address priority used by the connection stage."""

    def _inbound(self, share_addr: str = "") -> dict[str, object]:
        return {"shareAddr": share_addr}

    def _full_config(self, tmp_path: Path) -> Config:
        """A config whose yggdrasil address file does not exist.

        The file path points into the temporary directory, so the test
        never reads the real node address of the machine it runs on.
        """

        return make_config(
            yggdrasil_address_file_path=tmp_path / "yggdrasil_self_address"
        )

    def _mock_sources(
        self,
        monkeypatch: pytest.MonkeyPatch,
        public: tuple[str, ...] = (),
        local: tuple[str, ...] = (),
        forwarded: str | None = None,
    ) -> None:
        """Replace the three address sources with fixed values."""

        monkeypatch.setattr(
            xui,
            "fetch_public_addresses",
            lambda _s, _q, _c: _addresses(ipv4=public),
        )
        monkeypatch.setattr(xui, "local_addresses", lambda _t: local)
        monkeypatch.setattr(
            "pyntara.upnp.forward_inbound_port", lambda *_a, **_k: forwarded
        )

    def test_prefers_a_public_address_that_belongs_to_the_machine(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A white address really sits on an interface: nothing else is
        # consulted, because the machine is reachable directly.
        self._mock_sources(
            monkeypatch, public=("203.0.113.5",), local=("203.0.113.5", "10.0.0.1")
        )
        cfg = make_config().three_x_ui_xray_setup
        assert (
            xui._server_share_address(
                cfg, self._full_config(tmp_path), self._inbound(), 30.0
            )
            == "203.0.113.5"
        )

    def test_uses_the_yggdrasil_address_for_a_foreign_public_address(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The reported address belongs to the provider NAT, not to this
        # machine, so a mesh address beats a private one.
        self._mock_sources(
            monkeypatch, public=("190.55.165.52",), local=("192.168.1.5",)
        )
        address_file = tmp_path / "yggdrasil_self_address"
        address_file.write_text("2001:db8::9\n", encoding="utf-8")
        cfg = make_config().three_x_ui_xray_setup
        full_config = make_config(yggdrasil_address_file_path=address_file)
        assert (
            xui._server_share_address(cfg, full_config, self._inbound(), 30.0)
            == "[2001:db8::9]"
        )

    def test_uses_the_router_address_when_upnp_forwards_the_port(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # UPnP opened the port, so the router address is reachable.
        self._mock_sources(
            monkeypatch,
            public=("190.55.165.52",),
            local=("192.168.1.5",),
            forwarded="190.55.165.52",
        )
        cfg = make_config().three_x_ui_xray_setup
        assert (
            xui._server_share_address(
                cfg, self._full_config(tmp_path), self._inbound(), 30.0
            )
            == "190.55.165.52"
        )

    def test_uses_the_local_address_when_nothing_else_answers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # No white address, no UPnP, no yggdrasil: the server still works
        # for the local network instead of writing no address at all.
        self._mock_sources(monkeypatch, local=("192.168.1.5",))
        cfg = make_config().three_x_ui_xray_setup
        assert (
            xui._server_share_address(
                cfg, self._full_config(tmp_path), self._inbound(), 30.0
            )
            == "192.168.1.5"
        )

    def test_keeps_the_share_address_stored_in_the_panel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._mock_sources(monkeypatch)
        cfg = make_config().three_x_ui_xray_setup
        assert (
            xui._server_share_address(
                cfg,
                self._full_config(tmp_path),
                self._inbound("198.51.100.9"),
                30.0,
            )
            == "198.51.100.9"
        )

    def test_returns_none_without_any_source(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._mock_sources(monkeypatch)
        cfg = make_config().three_x_ui_xray_setup
        assert (
            xui._server_share_address(
                cfg, self._full_config(tmp_path), self._inbound(), 30.0
            )
            is None
        )


class TestUpnpClientPackage:
    """Tests for the package installation the task owns."""

    def test_skips_installation_when_the_package_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_install(_package: str, _timeout: float) -> tuple[bool, str]:
            raise AssertionError("apt must not run for an installed package")

        monkeypatch.setattr(xui, "package_is_installed", lambda _p, _t: True)
        monkeypatch.setattr(xui, "install_package_once", fail_install)
        cfg = make_config().three_x_ui_xray_setup
        assert xui._ensure_upnp_client(cfg, 30.0) is True

    def test_installs_the_configured_package_through_the_shared_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed: list[str] = []

        def fake_install(package: str, _timeout: float) -> tuple[bool, str]:
            installed.append(package)
            return (True, "")

        monkeypatch.setattr(xui, "package_is_installed", lambda _p, _t: False)
        monkeypatch.setattr(xui, "install_package_once", fake_install)
        cfg = make_config(three_x_ui_upnp_package="miniupnpc").three_x_ui_xray_setup
        assert xui._ensure_upnp_client(cfg, 30.0) is True
        assert installed == ["miniupnpc"]

    def test_reports_failure_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(xui, "package_is_installed", lambda _p, _t: False)
        monkeypatch.setattr(
            xui, "install_package_once", lambda _p, _t: (False, "no candidate")
        )
        cfg = make_config().three_x_ui_xray_setup
        assert xui._ensure_upnp_client(cfg, 30.0) is False


class TestUpnpForwardingCall:
    """Tests for the task side of the UPnP attempt."""

    def test_does_not_touch_upnp_when_it_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The switch is in the config, so a machine that must not talk to
        # its router never installs the client or asks for a mapping.
        def fail_forward(*args: object, **kwargs: object) -> str | None:
            raise AssertionError("UPnP must not be attempted")

        monkeypatch.setattr(
            "pyntara.upnp.forward_inbound_port", fail_forward
        )
        monkeypatch.setattr(
            xui, "fetch_public_addresses", lambda _s, _q, _c: _addresses()
        )
        monkeypatch.setattr(xui, "local_addresses", lambda _t: ())
        cfg = make_config(three_x_ui_upnp_enabled=False).three_x_ui_xray_setup
        full_config = make_config(
            yggdrasil_address_file_path=tmp_path / "yggdrasil_self_address"
        )
        assert (
            xui._server_share_address(
                cfg, full_config, {"shareAddr": ""}, 30.0
            )
            is None
        )

    def test_passes_the_config_values_to_the_shared_helper(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[tuple[object, ...]] = []

        def fake_forward(*args: object, **kwargs: object) -> str:
            calls.append(args)
            return "190.55.165.52"

        monkeypatch.setattr("pyntara.upnp.forward_inbound_port", fake_forward)
        monkeypatch.setattr(
            xui,
            "fetch_public_addresses",
            lambda _s, _q, _c: _addresses(ipv4=("190.55.165.52",)),
        )
        monkeypatch.setattr(xui, "local_addresses", lambda _t: ("192.168.1.5",))
        cfg = make_config().three_x_ui_xray_setup
        full_config = make_config(
            yggdrasil_address_file_path=tmp_path / "yggdrasil_self_address"
        )
        assert (
            xui._server_share_address(cfg, full_config, {"shareAddr": ""}, 30.0)
            == "190.55.165.52"
        )
        assert calls[0][:4] == ("upnpc", "pyntara xray", 443, "TCP")
        assert calls[0][4] == ("190.55.165.52",)
