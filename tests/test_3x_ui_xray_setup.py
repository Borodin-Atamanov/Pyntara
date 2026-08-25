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
) -> list[list[str]]:
    """Install a subprocess.run fake; return the recorded command calls.

    curl answers the release API and writes the fixture installer script,
    bash runs it (failing when installer_fails), systemctl reports the
    enabled and active state from the flags, and the version query
    answers installed_version. With active_becomes, the service turns
    active after the installer runs; without it, the readiness loop runs
    out. With missing_binary, the version query raises FileNotFoundError
    like a real missing executable. When captured_env is given, the env
    dict of every bash call is appended to it.
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
            captured.append((port, service_name, kwargs.get("service_process_name")))

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
            captured.append((port, service_name, kwargs.get("service_process_name")))

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


class TestStageSsl:
    """Tests for stage 4: the Let's Encrypt IP certificate setup."""

    def _cfg(
        self, tmp_path: Path, *, ssl_enabled: bool = True
    ) -> object:
        config = make_config(
            task_data_root=tmp_path,
            three_x_ui_install_dir=tmp_path / "usr" / "local" / "x-ui",
            three_x_ui_ssl_enabled=ssl_enabled,
        )
        return config.three_x_ui_xray_setup

    def test_stage_ssl_skipped_when_disabled(self, tmp_path: Path) -> None:
        # ssl_enabled=False disables the whole stage.
        assert xui._stage_ssl(self._cfg(tmp_path, ssl_enabled=False), 30) is None

    def test_stage_ssl_does_nothing_when_cert_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A panel that already has a certificate is left alone.
        monkeypatch.setattr(
            xui, "_panel_cert_value", lambda _cfg, _timeout: "/root/cert/ip/fullchain.pem"
        )
        assert xui._stage_ssl(self._cfg(tmp_path), 30) is None

    def test_stage_ssl_warns_when_no_ip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No public address can be detected: the stage warns and changes
        # nothing.
        monkeypatch.setattr(xui, "_panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(xui, "_detect_server_ip", lambda _timeout: None)
        result = xui._stage_ssl(self._cfg(tmp_path), 30)
        assert result is not None
        assert result.changed is False
        assert any("public IPv4" in w for w in result.warnings or ())

    def test_stage_ssl_issues_cert_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No certificate: the stage detects the IP, frees the ACME port
        # and issues the certificate.
        seen: dict[str, object] = {}

        def fake_issue(_cfg: object, ip: str, _timeout: float) -> tuple[bool, str]:
            seen["ip"] = ip
            return True, "certificate issued"

        monkeypatch.setattr(xui, "_panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(xui, "_detect_server_ip", lambda _timeout: "203.0.113.5")
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
        monkeypatch.setattr(xui, "_panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(xui, "_detect_server_ip", lambda _timeout: "203.0.113.5")
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

    def test_issue_ip_certificate_runs_acme_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The acme.sh command sequence mirrors the installer: create the
        # certificate directory, issue, installcert and point the panel
        # at the files.
        calls: list[list[str]] = []
        cert_dir = tmp_path / "cert"
        cert_full = cert_dir / "fullchain.pem"
        cert_key = cert_dir / "privkey.pem"
        monkeypatch.setattr(xui, "_ensure_acme", lambda _timeout: True)
        monkeypatch.setattr(xui, "_acme_path", lambda: Path("/tmp/acme.sh"))
        monkeypatch.setattr(xui, "CERT_DIR", cert_dir)
        monkeypatch.setattr(xui, "CERT_FULLCHAIN", cert_full)
        monkeypatch.setattr(xui, "CERT_PRIVKEY", cert_key)

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            if command[1] == "--installcert":
                cert_full.write_text("fullchain", encoding="utf-8")
                cert_key.write_text("privkey", encoding="utf-8")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        cfg = self._cfg(tmp_path)
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
        assert ["systemctl", "restart", "x-ui.service"] in calls
        assert cert_key.stat().st_mode & 0o777 == 0o600
        assert cert_full.stat().st_mode & 0o777 == 0o644
        assert message == "certificate issued"

    def test_issue_ip_certificate_warns_on_step_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failed acme.sh step reports a failure message.
        monkeypatch.setattr(xui, "_ensure_acme", lambda _timeout: True)
        monkeypatch.setattr(xui, "_acme_path", lambda: Path("/tmp/acme.sh"))
        monkeypatch.setattr(xui, "CERT_DIR", tmp_path / "cert")

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            if command[1] == "--issue":
                return _FakeProc(1, "error")
            return _FakeProc(0)

        monkeypatch.setattr(
            "pyntara.tasks.three_x_ui_xray_setup.run_command", fake_run
        )
        ok, message = xui._issue_ip_certificate(self._cfg(tmp_path), "203.0.113.5", 30)
        assert ok is False
        assert "acme.sh step failed" in message

    def test_rerun_invokes_stage_ssl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # On a rerun (target state reached, installer skipped) the task
        # runs stage 4.
        called: list[bool] = []
        monkeypatch.setattr(
            xui,
            "_stage_ssl",
            lambda _cfg, _timeout: (called.append(True), None)[1],
        )
        _stage2_fake(monkeypatch, tmp_path)
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
        assert called == [True]

    def test_rerun_with_ssl_issue_reports_changed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A rerun that issues the missing certificate reports changed.
        monkeypatch.setattr(xui, "_panel_cert_value", lambda _cfg, _timeout: None)
        monkeypatch.setattr(xui, "_detect_server_ip", lambda _timeout: "203.0.113.5")
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
        )
        result = xui.task(ctx)
        assert result.success is True
        assert result.changed is True
