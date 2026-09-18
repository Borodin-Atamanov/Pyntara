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
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from support import FakeProc as _FakeProc
from support import make_context

from pyntara import routing_policy, xray_client
from pyntara import xui as xui_client
from pyntara.context import Context
from pyntara.location import CountryReport
from pyntara.models import TaskResult
from pyntara.public_address import PublicAddresses
from pyntara.upnp import ForwardedAddress
from pyntara.utils import curl_flags
from pyntara.values import engine as engine_values
from pyntara.values import three_x_ui_xray_setup as panel_values
from pyntara.values import yggdrasil_service_setup as yggdrasil_values
from pyntara.xray_facts import _RunFacts as RunFacts

xui = importlib.import_module("pyntara.tasks.three_x_ui_xray_setup")
xray_facts = importlib.import_module("pyntara.xray_facts")
xray_panel = importlib.import_module("pyntara.xray_panel")
xray_certificate = importlib.import_module("pyntara.xray_certificate")
xray_inbound = importlib.import_module("pyntara.xray_inbound")
xray_local_proxy = importlib.import_module("pyntara.xray_local_proxy")

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
        lambda env, timeout: login_ok,
    )

    # Mock stage 3 API functions.
    if inbound_exists:
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port",
            lambda env, _port, timeout: {
                "id": 1,
                "port": _port,
                "protocol": "vless",
            },
        )
    else:
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port",
            lambda env, port, timeout: None,
        )

    if keygen_ok:
        monkeypatch.setattr(
            "pyntara.xui.generate_reality_key",
            lambda env, timeout: ("priv123", "pub123"),
        )
    else:
        monkeypatch.setattr(
            "pyntara.xui.generate_reality_key",
            lambda env, timeout: None,
        )

    monkeypatch.setattr(
        "pyntara.xui.create_inbound",
        lambda env, payload, timeout: (create_inbound_ok, "inbound created"),
    )

    # Mock the runtime vault opener.
    if vault_ok:
        fake_kp = Mock()
        fake_kp.find_entries.return_value = None
        fake_kp.root_group = Mock()
        fake_kp.add_entry = Mock()
        fake_kp.save = Mock()
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda: fake_kp)
    else:
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda: None)


def _use_temporary_panel_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Point every write path of the section at the temporary tree of a test.

    The panel binary, the install-result file and the certificate pairs
    live under real paths on the target machine, and the file paths of a
    pair are derived from its directory when the values module is imported,
    so a test declares both the directories and the files instead of
    writing outside the test bench.
    """

    install_dir = tmp_path / "usr" / "local" / "x-ui"
    install_result_env = tmp_path / "etc" / "x-ui" / "install-result.env"
    cert_dir = tmp_path / "cert"
    self_signed_cert_dir = tmp_path / "selfsigned"
    monkeypatch.setattr(panel_values, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(panel_values, "INSTALL_RESULT_ENV_PATH", install_result_env)
    monkeypatch.setattr(panel_values, "CERT_DIR", cert_dir)
    monkeypatch.setattr(panel_values, "CERT_FULLCHAIN_PATH", cert_dir / "fullchain.pem")
    monkeypatch.setattr(panel_values, "CERT_PRIVKEY_PATH", cert_dir / "privkey.pem")
    monkeypatch.setattr(panel_values, "SELF_SIGNED_CERT_DIR", self_signed_cert_dir)
    monkeypatch.setattr(
        panel_values,
        "SELF_SIGNED_CERT_FULLCHAIN_PATH",
        self_signed_cert_dir / "fullchain.pem",
    )
    monkeypatch.setattr(
        panel_values,
        "SELF_SIGNED_CERT_PRIVKEY_PATH",
        self_signed_cert_dir / "privkey.pem",
    )


def _ctx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    force: bool = False,
    service_wait_seconds: int = 0,
    readiness_delay: int = 0,
) -> Context:
    """Context for a test, with the section values of the run declared.

    The panel binary, the install-result file and the self-signed
    certificates are temporary, and the two wait budgets are what the
    caller asks for, so a stage that runs against the real machine paths
    cannot happen here.
    """

    _use_temporary_panel_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        panel_values, "SERVICE_START_WAIT_SECONDS", service_wait_seconds
    )
    monkeypatch.setattr(
        panel_values, "PANEL_LISTENER_WAIT_SECONDS", service_wait_seconds
    )
    monkeypatch.setattr(
        panel_values, "READINESS_CHECK_DELAY_SECONDS", readiness_delay
    )
    return make_context(
        task_name="three_x_ui_xray_setup",
        install_mode="server",
        force_tasks=frozenset({"three_x_ui_xray_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
    )


def _facts(
    public: tuple[str, ...] = (),
    local: tuple[str, ...] = (),
    router: str | None = None,
    client: str | None = None,
    public_ipv6: tuple[str, ...] = (),
) -> RunFacts:
    """Run facts for tests: no network, no router unless asked."""

    return RunFacts(
        public_addresses=PublicAddresses(ipv4=public, ipv6=public_ipv6),
        local_addresses=local,
        router_address=router,
        client_address=client,
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
    monkeypatch.setattr(xui, "_collect_run_facts", lambda _t: _facts())
    monkeypatch.setattr(xui, "_forward_upnp_ports", lambda _f, _t: None)
    if mock_stage_ssl:
        monkeypatch.setattr(xui, "_stage_ssl", lambda _timeout, _facts: None)
    if mock_takeover:
        monkeypatch.setattr(
            xui, "_takeover_credentials", lambda _t, _creds: (False, "")
        )
    if mock_settings:
        monkeypatch.setattr(
            "pyntara.xui.ensure_subscription_paths",
            lambda env, timeout: (False, ""),
        )
    if mock_connection:
        monkeypatch.setattr(
            xui,
            "_stage_connection",
            lambda _timeout, _facts, **_kwargs: None,
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
    monkeypatch.setattr(xui, "_collect_run_facts", lambda _t: _facts())
    monkeypatch.setattr(xui, "_forward_upnp_ports", lambda _f, _t: None)
    if mock_stage_ssl:
        monkeypatch.setattr(xui, "_stage_ssl", lambda _timeout, _facts: None)
    if mock_takeover:
        monkeypatch.setattr(
            xui, "_takeover_credentials", lambda _t, _creds: (False, "")
        )
    if mock_settings:
        monkeypatch.setattr(
            "pyntara.xui.ensure_subscription_paths",
            lambda env, timeout: (False, ""),
        )
    if mock_connection:
        monkeypatch.setattr(
            xui,
            "_stage_connection",
            lambda _timeout, _facts, **_kwargs: None,
        )
    return calls


def test_already_configured_does_not_run_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installed version equals the newest release and the service is
    # enabled and active: the task returns done with changed=False and
    # never invokes the official installer. Stage 2 runs and succeeds.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
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
    assert (result.message or "").startswith("target state already reached")
    assert "inbound created" in (result.message or "")
    expected_flags = curl_flags(
        engine_values.CURL_TIMEOUT_SECONDS,
        engine_values.CURL_RETRIES,
        engine_values.CURL_CONNECT_TIMEOUT_SECONDS,
        engine_values.CURL_RETRY_MAX_TIME_SECONDS,
        engine_values.CURL_RETRY_DELAY_SECONDS,
    )
    release_calls = [
        call
        for call in calls
        if call[0] == "curl" and "releases/latest" in " ".join(call)
    ]
    assert release_calls
    assert all(flag in release_calls[0] for flag in expected_flags)
    assert not any(call[0] == "bash" for call in calls)


def test_installs_when_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 3x-ui is not installed and the service is not enabled or active:
    # the task downloads the official installer, runs it non-interactively
    # and waits for the service to become active. Stage 2 runs after.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
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


def test_installs_new_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # An older release is installed: the task runs the installer and
    # waits for the service to become active again. Stage 2 runs after.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
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
    ctx = _ctx(monkeypatch, tmp_path)
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
    ctx = _ctx(monkeypatch, tmp_path, force=True)
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


def test_installer_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The official installer exits nonzero: the task reports the failure as
    # a warning and the panel stages still run against the panel that is
    # there.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=None,
        enabled=False,
        active=False,
        installer_fails=True,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert any("installer failed" in warning for warning in result.warnings)
    assert any(call[0] == "bash" for call in calls)


def test_service_never_active_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installer ran but the service never becomes active within the
    # readiness budget: the reason is a warning and the panel stages still
    # report their own result.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    calls = _install_fake(
        monkeypatch,
        install_dir=tmp_path / "usr" / "local" / "x-ui",
        installed_version=None,
        enabled=False,
        active=False,
        active_becomes=False,
    )
    result = xui.task(ctx)
    assert result.success is True
    assert any("did not become active" in warning for warning in result.warnings)
    assert any(call[0] == "bash" for call in calls)


def test_release_json_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The GitHub releases API is unreachable: the task reports the reason
    # and skips the installer, while the panel stages still run.
    # Run facts are stubbed like every other external resource: the real
    # helper queries the public address echo services through
    # subprocess.Popen, which the curl fake below does not intercept.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    monkeypatch.setattr(xui, "_collect_run_facts", lambda _t: _facts())

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        if command[0] == "curl":
            return _FakeProc(7, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = xui.task(ctx)
    assert result.success is True
    assert any("cannot fetch" in warning for warning in result.warnings)


def test_an_install_warning_is_reported_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The install step reports its warning inside its own result, and the
    # merged result must not repeat it: an operator who reads the same
    # sentence twice cannot tell one problem from two.
    _stage2_fake(monkeypatch, tmp_path)
    ctx = _ctx(monkeypatch, tmp_path)
    monkeypatch.setattr(xui, "_collect_run_facts", lambda _t: _facts())

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        if command[0] == "curl":
            return _FakeProc(7, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    result = xui.task(ctx)
    fetch_warnings = [
        warning for warning in result.warnings or () if "cannot fetch" in warning
    ]
    assert len(fetch_warnings) == 1


def test_stage2_login_failure_reports_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The installer succeeded but stage 2 login fails: the task returns
    # success with warnings.
    _stage2_fake(monkeypatch, tmp_path, login_ok=False)
    ctx = _ctx(monkeypatch, tmp_path)
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
    ctx = _ctx(monkeypatch, tmp_path)
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
        ctx = _ctx(monkeypatch, tmp_path)
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
        # nothing. The client half is stubbed here: its own tests cover it,
        # and this one is about stage 3.
        monkeypatch.setattr(xui, "_stage_local_proxy", lambda *_a, **_k: None)
        monkeypatch.setattr(xui, "_stage_routing_policy", lambda *_a, **_k: None)
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True)
        ctx = _ctx(monkeypatch, tmp_path)
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
        ctx = _ctx(monkeypatch, tmp_path)
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
        ctx = _ctx(monkeypatch, tmp_path)
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
        # with three dash separators, panel port from the values.
        env = xray_panel._credential_env()
        proquint_letters = frozenset("bdfghjklmnprstvzaiou")
        assert env["XUI_PANEL_PORT"] == str(panel_values.PANEL_PORT)
        assert len(env["XUI_USERNAME"]) == 10
        assert set(env["XUI_USERNAME"]) <= proquint_letters
        assert len(env["XUI_PASSWORD"]) == 20
        assert set(env["XUI_PASSWORD"]) <= proquint_letters
        assert len(env["XUI_WEB_BASE_PATH"]) == 23
        assert env["XUI_WEB_BASE_PATH"].count("-") == 3
        assert set(env["XUI_WEB_BASE_PATH"].replace("-", "")) <= proquint_letters

    def test_random_value_sizes_come_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The length of the random part of every generated value is a
        # declared value: two bytes instead of four halve the proquint
        # strings, and the subscription id follows its own value.
        monkeypatch.setattr(panel_values, "RANDOM_USERNAME_BYTES", 2)
        monkeypatch.setattr(panel_values, "RANDOM_SECRET_BYTES", 4)
        monkeypatch.setattr(panel_values, "RANDOM_SUB_ID_BYTES", 3)
        env = xray_panel._credential_env()
        assert len(env["XUI_USERNAME"]) == 5
        assert len(env["XUI_PASSWORD"]) == 10
        assert len(env["XUI_WEB_BASE_PATH"]) == 11
        email, client_id, sub_id = xray_inbound._client_identity({}, [])
        assert len(email) == 5
        assert len(client_id) == 11
        assert len(sub_id) == 11

    def test_client_identity_reuses_the_stored_values(self, tmp_path: Path) -> None:
        # A stored identity wins over a generated one, so a rerun reuses the
        # client the panel already knows.
        stored = {"CLIENT_EMAIL": "a", "CLIENT_ID": "b", "SUB_ID": "c"}
        assert xray_inbound._client_identity(stored, []) == ("a", "b", "c")

    def test_client_identity_adopts_the_client_the_panel_serves(
        self, tmp_path: Path
    ) -> None:
        # The vault carries no identity while the panel already serves one:
        # the identity of that client is used, so the task does not add a
        # second client to the same inbound.
        served = [{"email": "kazoj-nogur", "id": "uuid-1", "subId": "sub-1"}]
        assert xray_inbound._client_identity({}, served) == (
            "kazoj-nogur",
            "uuid-1",
            "sub-1",
        )

    def test_installer_receives_credential_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The installer runs with XUI_NONINTERACTIVE plus the proquint
        # credentials, the fixed panel port and the SSL mode in its
        # environment.
        envs: list[dict[str, str]] = []
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path)
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

    def test_installer_runs_without_the_project_venv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The installer is a third-party shell script that resolves python3
        # from PATH. The engine runs inside the project venv, whose bin
        # directory would win that lookup with an interpreter that carries
        # none of the installer dependencies, so the venv leaves PATH and
        # VIRTUAL_ENV is cleared.
        venv_root = tmp_path / "venv"
        venv_bin = venv_root / "bin"
        monkeypatch.setenv("VIRTUAL_ENV", str(venv_root))
        monkeypatch.setenv(
            "PATH",
            ":".join([str(venv_bin), str(venv_root), "/usr/bin", "/bin"]),
        )
        envs: list[dict[str, str]] = []
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path)
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
        installer_env = envs[0]
        assert installer_env["PATH"].split(":") == ["/usr/bin", "/bin"]
        assert installer_env["VIRTUAL_ENV"] == ""

    def test_installer_omits_ssl_mode_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # With ssl_enabled=False the installer env has no XUI_SSL_MODE,
        # so the panel stays HTTP.
        envs: list[dict[str, str]] = []
        _stage2_fake(monkeypatch, tmp_path)
        context = _ctx(monkeypatch, tmp_path)
        monkeypatch.setattr(panel_values, "SSL_ENABLED", 0)
        _install_fake(
            monkeypatch,
            install_dir=panel_values.INSTALL_DIR,
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
            captured_env=envs,
        )
        result = xui.task(context)
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
                (
                    port,
                    service_name,
                    cast(str | None, kwargs.get("service_process_name")),
                )
            )

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xray_panel, "ensure_port_free", fake_ensure_port_free)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path)
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

    def test_the_panel_binary_name_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The file name of the panel binary and the argv of the version
        # probe are declared values: another name and another flag in the
        # section are the argv the task runs.
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(panel_values, "BINARY_FILE_NAME", "my-x-ui")
        monkeypatch.setattr(
            panel_values, "PANEL_VERSION_COMMAND", ("{binary}", "--version")
        )
        probed: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            probed.append(list(command))
            return _FakeProc(0, "3.7.0\n")

        monkeypatch.setattr(xray_panel, "run_command", fake_run)
        assert xray_panel._installed_version(30.0) == "3.7.0"
        assert probed == [
            [str(panel_values.INSTALL_DIR / "my-x-ui"), "--version"]
        ]

    def test_a_panel_command_template_is_filled_from_the_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The flags of a panel call and the placeholders it carries are
        # declared values: every call fills the path of the binary as
        # {binary} and its own values as their own placeholders.
        monkeypatch.setattr(
            panel_values,
            "PANEL_PORT_COMMAND",
            (
                "{binary}",
                "setting",
                "--port",
                "{port}",
                "--quiet",
            ),
        )
        assert xray_panel._panel_command(
            panel_values.PANEL_PORT_COMMAND, port="1234"
        ) == [
            str(panel_values.INSTALL_DIR / panel_values.BINARY_FILE_NAME),
            "setting",
            "--port",
            "1234",
            "--quiet",
        ]

    def test_the_panel_process_name_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The process name the port helpers look for is a declared value:
        # another name in the section is what the task hands to them.
        captured: list[str | None] = []

        def fake_ensure_port_free(
            port: int,
            service_name: str,
            _timeout: float,
            **kwargs: object,
        ) -> None:
            captured.append(cast(str | None, kwargs.get("service_process_name")))

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xray_panel, "ensure_port_free", fake_ensure_port_free)
        _stage2_fake(monkeypatch, tmp_path)
        context = _ctx(monkeypatch, tmp_path)
        monkeypatch.setattr(
            panel_values, "SERVICE_PROCESS_NAME", "my-panel-process"
        )
        _install_fake(
            monkeypatch,
            install_dir=panel_values.INSTALL_DIR,
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
        )
        result = xui.task(context)
        assert result.success is True
        assert captured == ["my-panel-process", "my-panel-process"]

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
                (
                    port,
                    service_name,
                    cast(str | None, kwargs.get("service_process_name")),
                )
            )

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xray_panel, "ensure_port_free", fake_ensure_port_free)
        _stage2_fake(monkeypatch, tmp_path)
        context = _ctx(monkeypatch, tmp_path)
        monkeypatch.setattr(panel_values, "SSL_ENABLED", 0)
        _install_fake(
            monkeypatch,
            install_dir=panel_values.INSTALL_DIR,
            installed_version=None,
            enabled=False,
            active=False,
            active_becomes=True,
        )
        result = xui.task(context)
        assert result.success is True
        assert captured == [(35353, "x-ui.service", "x-ui")]

    def test_port_free_failure_is_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The panel port stays occupied after the free attempt: the task
        # reports the reason and skips the installer, while the panel
        # stages still run.
        def fake_ensure_port_free(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("still occupied")

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xray_panel, "ensure_port_free", fake_ensure_port_free)
        ctx = _ctx(monkeypatch, tmp_path)
        calls = _install_fake(
            monkeypatch,
            install_dir=tmp_path / "usr" / "local" / "x-ui",
            installed_version=None,
            enabled=False,
            active=False,
        )
        result = xui.task(ctx)
        assert result.success is True
        assert any("still occupied" in warning for warning in result.warnings)
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

        def fake_ensure_port_free(port: int, *_args: object, **_kwargs: object) -> None:
            captured.append(port)

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xray_panel, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _t, _facts: False)
        monkeypatch.setattr(
            xui,
            "_stage_ssl",
            lambda _timeout, _facts: TaskResult(
                success=True,
                changed=True,
                message="panel serves HTTPS with a self-signed certificate",
            ),
        )
        monkeypatch.setattr(
            xui, "_converge_panel_port", lambda _timeout: (False, None)
        )
        monkeypatch.setattr(
            xui, "_sync_install_result_env", lambda _timeout: False
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path, force=True)
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

        def fake_ensure_port_free(port: int, *_args: object, **_kwargs: object) -> None:
            captured.append(port)

        monkeypatch.setattr(xui, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xray_panel, "ensure_port_free", fake_ensure_port_free)
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _t, _facts: True)
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda timeout: "/root/cert/ip/fullchain.pem",
        )
        monkeypatch.setattr(
            xui, "_converge_panel_port", lambda _timeout: (False, None)
        )
        monkeypatch.setattr(
            xui, "_sync_install_result_env", lambda _timeout: False
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path, force=True)
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
        monkeypatch.setattr(xui, "_ssl_reachable", lambda _t, _facts: True)
        monkeypatch.setattr(
            xui,
            "_stage_ssl",
            lambda _timeout, _facts: TaskResult(
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
            xui, "_converge_panel_port", lambda _timeout: (False, None)
        )
        monkeypatch.setattr(
            xui, "_sync_install_result_env", lambda _timeout: False
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path, force=True)
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

        def fake_stage_ssl(_timeout: float, _facts: object) -> None:
            order.append("stage_ssl")

        def fake_sync(_timeout: float) -> bool:
            order.append("sync")
            return False

        monkeypatch.setattr(xui, "_stage_ssl", fake_stage_ssl)
        monkeypatch.setattr(xui, "_sync_install_result_env", fake_sync)
        monkeypatch.setattr(
            xui, "_converge_panel_port", lambda _timeout: (False, None)
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path)
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

    def test_result_message_names_the_changed_stages(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A rerun that changes something must say what it changed: the
        # result used to carry only the installer state, so a run that
        # rewrote the inbound keys and the connection profile read as if
        # nothing had happened.
        monkeypatch.setattr(
            xui,
            "_stage3",
            lambda _template, _timeout: TaskResult(
                success=True, changed=True, message="inbound share data updated"
            ),
        )
        monkeypatch.setattr(
            xui,
            "_stage_connection",
            lambda _timeout, _facts, **_kwargs: TaskResult(
                success=True, changed=True, message="connection profile stored"
            ),
        )
        monkeypatch.setattr(xui, "_stage_local_proxy", lambda *_a, **_k: None)
        monkeypatch.setattr(xui, "_stage_routing_policy", lambda *_a, **_k: None)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path)
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
        assert result.message == (
            "target state already reached; inbound share data updated; "
            "connection profile stored"
        )


class TestPanelPortConvergence:
    """Tests for bringing the panel to the configured port."""


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

        monkeypatch.setattr("pyntara.xray_panel.run_command", fake_run)
        monkeypatch.setattr(xray_panel, "ensure_port_free", lambda *a, **k: None)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        changed, message = xray_panel._converge_panel_port(30)
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

        monkeypatch.setattr("pyntara.xray_panel.run_command", fake_run)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        changed, message = xray_panel._converge_panel_port(30)
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

        monkeypatch.setattr("pyntara.xray_panel.run_command", fake_run)
        monkeypatch.setattr(
            xray_panel,
            "ensure_port_free",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("still occupied")),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        with pytest.raises(RuntimeError):
            xray_panel._converge_panel_port(30)

    def test_converges_panel_port_after_install(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The installer left the panel on an old port: the task brings it
        # to the configured port and updates install-result.env.
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path, force=True)
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
        monkeypatch.setattr(xui, "_stage_ssl", lambda _timeout, _f: None)
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path)
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
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda timeout: "https")
        monkeypatch.setattr(
            panel_values, "INSTALL_RESULT_ENV_PATH", env_path
        )
        assert xray_panel._sync_install_result_env(30) is True
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
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda timeout: "http")
        monkeypatch.setattr(
            panel_values, "INSTALL_RESULT_ENV_PATH", env_path
        )
        assert xray_panel._sync_install_result_env(30) is False

    def test_wait_panel_http_returns_when_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel answers on the first poll: the wait returns True.
        monkeypatch.setattr(
            "pyntara.xray_panel.run_command",
            lambda *a, **k: _FakeProc(0, ""),
        )
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda timeout: "http")
        assert xray_panel._wait_panel_http(30) is True

    def test_wait_panel_http_retries_then_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel never answers: the wait asks again until its budget is
        # spent and then reports False. The clock is faked, so the budget
        # is spent by the pause and not by real time.
        clock = {"now": 0.0}

        def fake_sleep(seconds: int) -> None:
            clock["now"] += seconds

        monkeypatch.setattr(
            "pyntara.xray_panel.run_command",
            lambda *a, **k: _FakeProc(7, ""),
        )
        monkeypatch.setattr(xray_panel.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(xray_panel.time, "sleep", fake_sleep)
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda timeout: "http")
        assert xray_panel._wait_panel_http(30) is False

    def test_the_panel_listener_budget_and_pause_come_from_the_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The probe timeout, the budget in seconds and the pause come from
        # the config, so a slow link is not reported as an unreachable
        # panel; the clock is faked, so the pause is what moves it.
        commands: list[list[str]] = []
        sleeps: list[int] = []
        clock = {"now": 0.0}

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            commands.append(command)
            return _FakeProc(7, "")

        def fake_sleep(seconds: int) -> None:
            sleeps.append(seconds)
            clock["now"] += seconds

        monkeypatch.setattr("pyntara.xray_panel.run_command", fake_run)
        monkeypatch.setattr(xray_panel.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(xray_panel.time, "sleep", fake_sleep)
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda timeout: "http")
        monkeypatch.setattr(
            panel_values, "INSTALL_RESULT_ENV_PATH", tmp_path / "missing.env"
        )
        monkeypatch.setattr(panel_values, "PROBE_TIMEOUT_SECONDS", 90)
        monkeypatch.setattr(panel_values, "PANEL_LISTENER_WAIT_SECONDS", 5)
        monkeypatch.setattr(panel_values, "READINESS_CHECK_DELAY_SECONDS", 2)
        assert xray_panel._wait_panel_http(30) is False
        # A budget of five seconds with a pause of two asks at 0, 2, 4 and
        # 6 seconds and stops after the fourth answer: the budget is what
        # ends the wait, not a count of checks.
        assert len(commands) == 4
        assert sleeps == [2, 2, 2]
        assert commands[0][commands[0].index("--max-time") + 1] == "90"


class TestSslReachability:
    """Tests for deciding whether the HTTP-01 challenge can be served."""

    def test_is_private_ipv4_ranges(self) -> None:
        networks = panel_values.PRIVATE_IPV4_NETWORKS
        assert xray_certificate._is_private_ipv4("10.0.0.1", networks) is True
        assert xray_certificate._is_private_ipv4("172.16.0.1", networks) is True
        assert xray_certificate._is_private_ipv4("172.31.255.255", networks) is True
        assert xray_certificate._is_private_ipv4("172.32.0.1", networks) is False
        assert xray_certificate._is_private_ipv4("192.168.1.1", networks) is True
        assert xray_certificate._is_private_ipv4("203.0.113.5", networks) is False

    def test_private_networks_come_from_the_config(self) -> None:
        # The networks that count as private are a declared value: a machine
        # behind carrier-grade NAT adds 100.64.0.0/10 and the same address
        # changes its verdict, without a line of code changing.
        carrier_grade = ("100.64.0.0/10",)
        assert xray_certificate._is_private_ipv4("100.64.0.5", carrier_grade) is True
        assert xray_certificate._is_private_ipv4("100.64.0.5", ("10.0.0.0/8",)) is False


    def test_ssl_reachable_public_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A public address on the interface allows the attempt directly
        # and the port-80 probe is never run.
        def fail_probe(*args: object, **kwargs: object) -> bool:
            raise AssertionError("the port-80 probe must not run")

        monkeypatch.setattr(xray_certificate, "_probe_port_80_forward", fail_probe)
        facts = _facts(local=("203.0.113.5",))
        assert xray_certificate._ssl_reachable(30, facts) is True

    def test_ssl_reachable_private_with_forward(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Behind NAT with a confirmed port-80 forward: attempt SSL.
        monkeypatch.setattr(
            xray_certificate, "_probe_port_80_forward", lambda *_a, **_k: True
        )
        facts = _facts(local=("192.168.1.10",))
        assert xray_certificate._ssl_reachable(30, facts) is True

    def test_ssl_reachable_private_without_forward(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Behind NAT without a forward: skip SSL.
        monkeypatch.setattr(
            xray_certificate, "_probe_port_80_forward", lambda *_a, **_k: False
        )
        facts = _facts(local=("192.168.1.10",))
        assert xray_certificate._ssl_reachable(30, facts) is False

    def test_ssl_reachable_unknown_local_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unknown local address: the attempt is allowed, never skipped.
        monkeypatch.setattr(
            xray_certificate, "_probe_port_80_forward", lambda *_a, **_k: False
        )
        assert xray_certificate._ssl_reachable(30, _facts()) is True

    def test_probe_port_80_forward_confirmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The temporary listener answers a connection through the public
        # address: the forward is confirmed.
        fake_proc = Mock()
        monkeypatch.setattr(
            "pyntara.xray_certificate.subprocess.Popen",
            lambda *a, **k: fake_proc,
        )
        monkeypatch.setattr(xray_certificate.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            "pyntara.xray_certificate.run_command",
            lambda *a, **k: _FakeProc(0, "ok"),
        )
        facts = _facts(public=("203.0.113.5",))
        assert xray_certificate._probe_port_80_forward(30, facts) is True
        fake_proc.terminate.assert_called_once()

    def test_probe_port_80_forward_not_confirmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The connection fails (no forward): not confirmed.
        fake_proc = Mock()
        monkeypatch.setattr(
            "pyntara.xray_certificate.subprocess.Popen",
            lambda *a, **k: fake_proc,
        )
        monkeypatch.setattr(xray_certificate.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            "pyntara.xray_certificate.run_command",
            lambda *a, **k: _FakeProc(7, ""),
        )
        facts = _facts(public=("203.0.113.5",))
        assert xray_certificate._probe_port_80_forward(30, facts) is False
        fake_proc.terminate.assert_called_once()

    def test_probe_port_80_forward_needs_public_ip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No public address: the probe cannot confirm a forward.
        assert (
            xray_certificate._probe_port_80_forward(30, _facts()) is False
        )

    def test_probe_port_80_forward_uses_the_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The probe timeouts and the listener pause are declared values: a
        # few hardcoded seconds report a working service as unreachable on
        # a slow link, and the port-80 probe has its own budget.
        monkeypatch.setattr(panel_values, "PROBE_PORT_80_TIMEOUT_SECONDS", 90)
        monkeypatch.setattr(panel_values, "PROBE_LISTENER_START_SECONDS", 4)
        sleeps: list[int] = []
        commands: list[list[str]] = []
        fake_proc = Mock()
        monkeypatch.setattr(
            "pyntara.xray_certificate.subprocess.Popen",
            lambda *a, **k: fake_proc,
        )
        monkeypatch.setattr(
            xray_certificate.time, "sleep", lambda seconds: sleeps.append(seconds)
        )

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            commands.append(command)
            return _FakeProc(0, "ok")

        monkeypatch.setattr("pyntara.xray_certificate.run_command", fake_run)
        facts = _facts(public=("203.0.113.5",))
        assert xray_certificate._probe_port_80_forward(30, facts) is True
        assert sleeps == [4]
        command = commands[0]
        assert command[command.index("--connect-timeout") + 1] == "90"
        assert command[command.index("--max-time") + 1] == "90"
        fake_proc.wait.assert_called_once_with(timeout=panel_values.PROBE_TIMEOUT_SECONDS)


class TestStageSsl:
    """Tests for stage 4: ensuring the panel serves HTTPS."""


    def test_stage_ssl_skipped_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ssl_enabled=0 in the values disables the whole stage.
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(panel_values, "SSL_ENABLED", 0)
        assert (
            xray_certificate._stage_ssl(
                30, _facts()
            )
            is None
        )

    def test_stage_ssl_does_nothing_when_cert_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A panel that already carries a trusted or foreign certificate
        # is left alone.
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda timeout: "/root/cert/ip/fullchain.pem",
        )
        assert xray_certificate._stage_ssl(30, _facts()) is None

    def test_stage_ssl_installs_self_signed_when_no_ip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No public address can be detected, so a trusted certificate is
        # impossible: the stage installs a self-signed one.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda timeout: None)
        monkeypatch.setattr(
            xray_certificate,
            "_ensure_self_signed_cert",
            lambda timeout, facts: (
                True,
                "self-signed certificate configured",
            ),
        )
        result = xray_certificate._stage_ssl(30, _facts())
        assert result is not None
        assert result.changed is True
        assert "self-signed" in (result.message or "")

    def test_stage_ssl_installs_self_signed_when_not_reachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The machine is behind NAT without a port-80 forward: the stage
        # installs a self-signed certificate instead of leaving HTTP.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda timeout: None)
        monkeypatch.setattr(
            xray_certificate, "_ssl_reachable", lambda timeout, facts: False
        )
        monkeypatch.setattr(
            xray_certificate,
            "_ensure_self_signed_cert",
            lambda timeout, facts: (
                True,
                "self-signed certificate configured",
            ),
        )
        facts = _facts(public=("203.0.113.5",))
        result = xray_certificate._stage_ssl(30, facts)
        assert result is not None
        assert result.changed is True
        assert "self-signed" in (result.message or "")

    def test_stage_ssl_keeps_self_signed_when_unreachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel already serves our self-signed certificate and port
        # 80 is still unreachable: nothing changes.
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda timeout: str(panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH),
        )
        monkeypatch.setattr(
            xray_certificate, "_ssl_reachable", lambda timeout, facts: False
        )
        assert xray_certificate._stage_ssl(30, _facts()) is None

    def test_stage_ssl_upgrades_self_signed_when_reachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel serves our self-signed certificate and port 80 has
        # become reachable: the stage replaces it with a trusted one.
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda timeout: str(panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH),
        )
        monkeypatch.setattr(
            xray_certificate, "_ssl_reachable", lambda timeout, facts: True
        )
        monkeypatch.setattr(
            xray_certificate,
            "_issue_ip_certificate",
            lambda ip, timeout: (True, "certificate issued"),
        )
        monkeypatch.setattr(xray_certificate, "ensure_port_free", lambda *a, **k: None)
        facts = _facts(public=("203.0.113.5",))
        result = xray_certificate._stage_ssl(30, facts)
        assert result is not None
        assert result.changed is True
        assert result.message == "SSL certificate configured"

    def test_stage_ssl_issues_cert_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No certificate and port 80 reachable: the stage frees the ACME
        # port and issues the certificate for the detected address.
        seen: dict[str, object] = {}

        def fake_issue(ip: str, _timeout: float) -> tuple[bool, str]:
            seen["ip"] = ip
            return True, "certificate issued"

        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda timeout: None)
        monkeypatch.setattr(
            xray_certificate, "_ssl_reachable", lambda timeout, facts: True
        )
        monkeypatch.setattr(xray_certificate, "_issue_ip_certificate", fake_issue)
        monkeypatch.setattr(xray_certificate, "ensure_port_free", lambda *a, **k: None)
        facts = _facts(public=("203.0.113.5",))
        result = xray_certificate._stage_ssl(30, facts)
        assert result is not None
        assert result.changed is True
        assert seen["ip"] == "203.0.113.5"

    def test_stage_ssl_warns_on_acme_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # acme.sh fails to issue the certificate: the stage warns.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda timeout: None)
        monkeypatch.setattr(
            xray_certificate, "_ssl_reachable", lambda timeout, facts: True
        )
        monkeypatch.setattr(
            xray_certificate,
            "_issue_ip_certificate",
            lambda ip, timeout: (False, "port 80 unreachable"),
        )
        monkeypatch.setattr(xray_certificate, "ensure_port_free", lambda *a, **k: None)
        facts = _facts(public=("203.0.113.5",))
        result = xray_certificate._stage_ssl(30, facts)
        assert result is not None
        assert result.changed is False
        assert any("SSL certificate setup failed" in w for w in result.warnings or ())

    def test_stage_ssl_warns_when_self_signed_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Neither a trusted nor a self-signed certificate can be set up
        # (openssl unavailable): the stage warns that the panel stays on
        # HTTP.
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda timeout: None)
        monkeypatch.setattr(
            xray_certificate,
            "_ensure_self_signed_cert",
            lambda timeout, facts: (
                False,
                "openssl unavailable: cannot generate a self-signed certificate",
            ),
        )
        result = xray_certificate._stage_ssl(30, _facts())
        assert result is not None
        assert result.changed is False
        assert any("panel serves HTTP" in w for w in result.warnings or ())

    def test_the_acme_commands_come_from_the_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The four acme.sh steps and the reload command they carry are
        # declared values: another template for each of them is the argv
        # the sequence runs, and the path of the tool comes from the
        # values as well.
        calls: list[list[str]] = []
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            xray_certificate, "_ensure_acme", lambda timeout: True
        )
        monkeypatch.setattr(
            xray_certificate, "_acme_path", lambda : Path("/my/acme.bin")
        )
        monkeypatch.setattr(
            panel_values, "ACME_SET_DEFAULT_CA_COMMAND", ("{acme}", "--ca")
        )
        monkeypatch.setattr(
            panel_values,
            "ACME_ISSUE_COMMAND",
            (
                "{acme}",
                "--issue",
                "-d",
                "{domain}",
                "--port",
                "{http_port}",
            ),
        )
        monkeypatch.setattr(
            panel_values,
            "ACME_INSTALLCERT_COMMAND",
            (
                "{acme}",
                "--install",
                "{key_file}",
                "{fullchain_file}",
                "{reload_command}",
            ),
        )
        monkeypatch.setattr(
            panel_values, "ACME_UPGRADE_COMMAND", ("{acme}", "--up")
        )
        monkeypatch.setattr(
            panel_values,
            "ACME_RELOAD_COMMAND",
            "my-restart {service_unit_name} || true",
        )

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            calls.append(list(command))
            if "--install" in command:
                panel_values.CERT_FULLCHAIN_PATH.write_text(
                    "fullchain", encoding="utf-8"
                )
                panel_values.CERT_PRIVKEY_PATH.write_text("privkey", encoding="utf-8")
            return _FakeProc(0, "")

        monkeypatch.setattr(xray_certificate, "run_command", fake_run)
        ok, message = xray_certificate._issue_ip_certificate("203.0.113.9", 30.0)
        assert ok is True
        assert message == "certificate issued"
        assert calls[:4] == [
            ["/my/acme.bin", "--ca"],
            [
                "/my/acme.bin",
                "--issue",
                "-d",
                "203.0.113.9",
                "--port",
                str(panel_values.ACME_PORT),
            ],
            [
                "/my/acme.bin",
                "--install",
                str(panel_values.CERT_PRIVKEY_PATH),
                str(panel_values.CERT_FULLCHAIN_PATH),
                f"my-restart {panel_values.SERVICE_UNIT_NAME} || true",
            ],
            ["/my/acme.bin", "--up"],
        ]

    def test_the_service_restart_comes_from_the_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The restart the task runs after changing the panel port is a
        # declared value, and so is the path of the acme.sh tool it uses.
        calls: list[list[str]] = []
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            panel_values,
            "SERVICE_RESTART_COMMAND",
            ("systemctl", "restart", "{service_unit_name}", "--no-block"),
        )

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            calls.append(list(command))
            return _FakeProc(0, "")

        monkeypatch.setattr(xray_panel, "run_command", fake_run)
        monkeypatch.setattr(xray_panel, "_actual_panel_port", lambda timeout: "1111")
        monkeypatch.setattr(xray_panel, "ensure_port_free", lambda *_a, **_k: None)
        monkeypatch.setattr(xray_panel, "_wait_panel_http", lambda *_a, **_k: True)
        xray_panel._converge_panel_port(30.0)
        assert [
            "systemctl",
            "restart",
            panel_values.SERVICE_UNIT_NAME,
            "--no-block",
        ] in calls

    def test_issue_ip_certificate_runs_acme_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The acme.sh command sequence mirrors the installer: create the
        # certificate directory, issue, installcert and point the panel
        # at the files.
        calls: list[list[str]] = []
        monkeypatch.setattr(
            xray_certificate, "_ensure_acme", lambda timeout: True
        )
        monkeypatch.setattr(
            xray_certificate, "_acme_path", lambda : Path("/tmp/acme.sh")
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        cert_dir = panel_values.CERT_DIR

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            if command[1] == "--installcert":
                panel_values.CERT_FULLCHAIN_PATH.write_text("fullchain", encoding="utf-8")
                panel_values.CERT_PRIVKEY_PATH.write_text("privkey", encoding="utf-8")
            return _FakeProc(0)

        monkeypatch.setattr("pyntara.xray_certificate.run_command", fake_run)
        ok, message = xray_certificate._issue_ip_certificate("203.0.113.5", 30)
        assert ok is True
        assert cert_dir.is_dir()
        assert any(
            command[1] == "--issue" and "203.0.113.5" in command for command in calls
        )
        assert any(command[1] == "--installcert" for command in calls)
        assert any(
            command[0] == str(panel_values.INSTALL_DIR / "x-ui") and command[1] == "cert"
            for command in calls
        )
        # The panel must restart after the cert paths are set to serve
        # TLS with the new certificate.
        assert ["systemctl", "restart", panel_values.SERVICE_UNIT_NAME] in calls
        assert panel_values.CERT_PRIVKEY_PATH.stat().st_mode & 0o777 == 0o600
        assert panel_values.CERT_FULLCHAIN_PATH.stat().st_mode & 0o777 == 0o644
        assert message == "certificate issued"

    def test_issue_ip_certificate_warns_on_step_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failed acme.sh step reports a failure message.
        monkeypatch.setattr(
            xray_certificate, "_ensure_acme", lambda timeout: True
        )
        monkeypatch.setattr(
            xray_certificate, "_acme_path", lambda : Path("/tmp/acme.sh")
        )
        monkeypatch.setattr(panel_values, "CERT_DIR", tmp_path / "cert")

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            if command[1] == "--issue":
                return _FakeProc(1, "error")
            return _FakeProc(0)

        monkeypatch.setattr("pyntara.xray_certificate.run_command", fake_run)
        ok, message = xray_certificate._issue_ip_certificate("203.0.113.5", 30)
        assert ok is False
        assert "acme.sh step failed" in message


class TestSelfSignedCert:
    """Tests for the self-signed certificate helper."""


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

        monkeypatch.setattr("pyntara.xray_certificate.run_command", fake_run)
        monkeypatch.setattr(
            "pyntara.xray_certificate.package_is_installed",
            lambda package, timeout: True,
        )
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda timeout: None)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        ok, message = xray_certificate._ensure_self_signed_cert(30, _facts())
        assert ok is True
        assert message == "self-signed certificate configured"
        assert any(command[0] == "openssl" and command[1] == "req" for command in calls)
        assert any(
            command[0] == str(panel_values.INSTALL_DIR / "x-ui")
            and command[1] == "cert"
            and str(panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH) in command
            for command in calls
        )
        assert ["systemctl", "restart", panel_values.SERVICE_UNIT_NAME] in calls
        assert panel_values.SELF_SIGNED_CERT_PRIVKEY_PATH.stat().st_mode & 0o777 == 0o600
        assert panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH.stat().st_mode & 0o777 == 0o644

    def test_noop_when_already_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel already points at our valid self-signed files: no
        # regeneration, no panel change.
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        panel_values.SELF_SIGNED_CERT_DIR.mkdir(parents=True, exist_ok=True)
        panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH.write_text("cert", encoding="utf-8")
        panel_values.SELF_SIGNED_CERT_PRIVKEY_PATH.write_text("key", encoding="utf-8")
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            return _FakeProc(0)

        monkeypatch.setattr("pyntara.xray_certificate.run_command", fake_run)
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda timeout: str(panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH),
        )
        ok, message = xray_certificate._ensure_self_signed_cert(30, _facts())
        assert ok is False
        assert message == ""
        assert not any(command[:2] == ["openssl", "req"] for command in calls)
        assert not any(
            command[:2] == [str(panel_values.INSTALL_DIR / "x-ui"), "cert"] for command in calls
        )
        assert ["systemctl", "restart", panel_values.SERVICE_UNIT_NAME] not in calls

    def test_does_not_touch_foreign_cert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel carries a certificate we do not own: leave it alone.
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(list(command))
            return _FakeProc(0)

        monkeypatch.setattr("pyntara.xray_certificate.run_command", fake_run)
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda timeout: "/root/cert/ip/fullchain.pem",
        )
        ok, message = xray_certificate._ensure_self_signed_cert(30, _facts())
        assert ok is False
        assert message == ""
        assert calls == []

    def test_warns_when_openssl_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # openssl cannot be installed: the helper reports the failure so
        # the caller falls back to the HTTP warning.
        monkeypatch.setattr(
            "pyntara.xray_certificate.package_is_installed",
            lambda package, timeout: False,
        )
        monkeypatch.setattr(
            "pyntara.xray_certificate.install_package_once",
            lambda package, timeout: (False, "apt failed"),
        )
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda timeout: None)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        ok, message = xray_certificate._ensure_self_signed_cert(30, _facts())
        assert ok is False
        assert "openssl unavailable" in message

    def test_regenerates_expired_cert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel points at our files but the certificate has expired:
        # the helper regenerates and re-applies it.
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        panel_values.SELF_SIGNED_CERT_DIR.mkdir(parents=True, exist_ok=True)
        panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH.write_text("old-cert", encoding="utf-8")
        panel_values.SELF_SIGNED_CERT_PRIVKEY_PATH.write_text("old-key", encoding="utf-8")
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

        monkeypatch.setattr("pyntara.xray_certificate.run_command", fake_run)
        monkeypatch.setattr(
            "pyntara.xray_certificate.package_is_installed",
            lambda package, timeout: True,
        )
        monkeypatch.setattr(
            "pyntara.xui.panel_cert_value",
            lambda timeout: str(panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH),
        )
        ok, _ = xray_certificate._ensure_self_signed_cert(30, _facts())
        assert ok is True
        assert any(command[0] == "openssl" and command[1] == "req" for command in calls)
        assert panel_values.SELF_SIGNED_CERT_FULLCHAIN_PATH.read_text(encoding="utf-8") == "new-cert"

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
        ctx = _ctx(monkeypatch, tmp_path)
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
        monkeypatch.setattr("pyntara.xui.panel_cert_value", lambda timeout: None)
        monkeypatch.setattr(
            xray_certificate,
            "_issue_ip_certificate",
            lambda ip, timeout: (True, "certificate issued"),
        )
        monkeypatch.setattr(xray_certificate, "ensure_port_free", lambda *a, **k: None)
        monkeypatch.setattr(
            xui,
            "_collect_run_facts",
            lambda _t: _facts(public=("203.0.113.5",)),
        )
        _stage2_fake(monkeypatch, tmp_path)
        ctx = _ctx(monkeypatch, tmp_path)
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
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda timeout: "https")
        monkeypatch.setattr(
            panel_values, "INSTALL_RESULT_ENV_PATH", env_path
        )
        env = xui_client.panel_environment(30)
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
        monkeypatch.setattr("pyntara.xui.login_and_verify", lambda env, timeout: True)
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda timeout: "https")
        fake_kp = Mock()
        fake_kp.find_entries.return_value = None
        fake_kp.root_group = Mock()
        fake_kp.add_entry = Mock()
        fake_kp.save = Mock()
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda: fake_kp)
        monkeypatch.setattr(
            panel_values, "INSTALL_RESULT_ENV_PATH", env_path
        )
        result = xray_panel._stage2(30)
        assert result is None
        url = fake_kp.add_entry.call_args.kwargs["url"]
        assert url.startswith("https://")


class TestRewriteEnv:
    """Tests for the shared ordered env-file rewrite helper."""

    def test_updates_values_preserving_order(self, tmp_path: Path) -> None:
        path = tmp_path / "env"
        path.write_text("A=1\nB=2\nC=3\n", encoding="utf-8")
        assert xray_panel._rewrite_env(path, {"B": "9", "D": "4"}) is True
        assert path.read_text(encoding="utf-8") == "A=1\nB=9\nC=3\nD=4\n"

    def test_noop_when_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "env"
        path.write_text("A=1\n", encoding="utf-8")
        assert xray_panel._rewrite_env(path, {"A": "1"}) is False
        assert path.read_text(encoding="utf-8") == "A=1\n"

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        assert xray_panel._rewrite_env(tmp_path / "nope", {"A": "1"}) is False


class TestTakeoverCredentials:
    """Tests for the force credential and webBasePath takeover."""


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

        monkeypatch.setattr("pyntara.xray_panel.run_command", fake_run)
        monkeypatch.setattr("pyntara.xui.panel_scheme", lambda timeout: "http")
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        creds = {
            "XUI_USERNAME": "newuser",
            "XUI_PASSWORD": "newpass",
            "XUI_WEB_BASE_PATH": "new-path-here",
        }
        ok, message = xray_panel._takeover_credentials(30, creds)
        assert ok is True
        assert "new-path-here" in message
        assert any(
            command[0] == str(panel_values.INSTALL_DIR / "x-ui")
            and command[1:3] == ["setting", "-username"]
            and "newuser" in command
            and "newpass" in command
            and "new-path-here" in command
            for command in calls
        )
        assert ["systemctl", "restart", panel_values.SERVICE_UNIT_NAME] in calls
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

        monkeypatch.setattr("pyntara.xray_panel.run_command", fake_run)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        ok, message = xray_panel._takeover_credentials(
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

        monkeypatch.setattr("pyntara.xray_panel.run_command", fake_run)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        ok, message = xray_panel._takeover_credentials(
            30,
            {"XUI_USERNAME": "u", "XUI_PASSWORD": "p", "XUI_WEB_BASE_PATH": "w"},
        )
        assert ok is False
        assert "cannot restart" in message


class TestForceTakeoverWiring:
    """Tests for the force takeover in the task flow."""

    def _takeover_fake(self, monkeypatch: pytest.MonkeyPatch, seen: list[bool]) -> None:
        def fake_takeover(
            _timeout: float, _creds: dict[str, str]
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
        ctx = _ctx(monkeypatch, tmp_path, force=True)
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
        ctx = _ctx(monkeypatch, tmp_path, force=True)
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
        ctx = _ctx(monkeypatch, tmp_path)
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
            lambda env, timeout: (
                True,
                "subscription paths set to /s/, /j/, /c/",
            ),
        )
        ctx = _ctx(monkeypatch, tmp_path)
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
            lambda env, timeout: (False, "cannot read panel settings"),
        )
        ctx = _ctx(monkeypatch, tmp_path)
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
            "pyntara.xui.find_inbound_by_port", lambda env, port, timeout: inbound
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound",
            lambda env, inbound, timeout: (True, "inbound updated"),
        )
        ctx = _ctx(monkeypatch, tmp_path)
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

    def test_issues_a_new_pair_when_the_private_key_is_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The public key is there and the private key is gone, which is a
        # pair no client can use. The panel is asked for a fresh pair and
        # both halves are written back.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True)
        inbound = self._inbound()
        stream = cast("dict[str, object]", inbound["streamSettings"])
        reality = cast("dict[str, object]", stream["realitySettings"])
        reality["settings"] = {
            "publicKey": "old-public",
            "fingerprint": "chrome",
        }
        reality.pop("privateKey", None)
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda env, port, timeout: inbound
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound",
            lambda env, inbound, timeout: (True, "inbound updated"),
        )
        ctx = _ctx(monkeypatch, tmp_path)
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
            "pyntara.xui.find_inbound_by_port", lambda env, port, timeout: inbound
        )
        ctx = _ctx(monkeypatch, tmp_path)
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
            "pyntara.xui.find_inbound_by_port", lambda env, port, timeout: inbound
        )
        monkeypatch.setattr("pyntara.xui.find_client", lambda env, email, timeout: None)
        monkeypatch.setattr(
            "pyntara.xui.create_client",
            lambda env, inbound_id, client_id, email, sub_id, timeout: (True, "client created"),
        )
        monkeypatch.setattr(
            "pyntara.xui.client_links",
            lambda env, email, timeout: ["vless://x@203.0.113.5:443"],
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound", lambda env, inbound, timeout: (True, "updated")
        )
        monkeypatch.setattr(
            xray_inbound, "_server_share_address", lambda inbound, facts: "203.0.113.5"
        )
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda: fake_kp)
        ctx = _ctx(monkeypatch, tmp_path)
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

    def test_restores_the_share_strategy_when_the_address_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The panel keeps the wanted address but carries the default
        # strategy, so the links it renders would use the host localhost.
        # The address matches, and only the strategy is wrong, so the task
        # must still write the inbound: a comparison of the address alone
        # would skip this repair.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True)
        inbound = self._inbound()
        inbound["shareAddrStrategy"] = "node"
        written: list[dict[str, object]] = []

        def record_update(
            _env: object, payload: dict[str, object], _timeout: object
        ) -> tuple[bool, str]:
            written.append(payload)
            return True, "updated"

        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda env, port, timeout: inbound
        )
        monkeypatch.setattr("pyntara.xui.update_inbound", record_update)
        monkeypatch.setattr(
            "pyntara.xui.find_client", lambda env, email, timeout: {"email": "a-b"}
        )
        monkeypatch.setattr(
            "pyntara.xui.client_links",
            lambda env, email, timeout: ["vless://x@203.0.113.5:443"],
        )
        monkeypatch.setattr(
            xray_inbound, "_server_share_address", lambda inbound, facts: "203.0.113.5"
        )
        ctx = _ctx(monkeypatch, tmp_path)
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
        assert written, "the inbound was never written"
        assert written[0]["shareAddrStrategy"] == "custom"
        assert written[0]["shareAddr"] == "203.0.113.5"

    def _serving_inbound(self, *emails: str) -> dict[str, object]:
        """The inbound of the stage with the given clients it serves."""

        inbound = self._inbound()
        inbound["settings"] = json.dumps(
            {
                "clients": [
                    {
                        "email": email,
                        "id": f"uuid-{email}",
                        "subId": f"sub-{email}",
                    }
                    for email in emails
                ],
                "decryption": "none",
            }
        )
        return inbound

    def test_reuses_the_client_the_panel_already_serves(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The vault carries no identity while the panel already serves one
        # client: the identity of that client is adopted and nothing is
        # created, so an inbound that must serve one client never gains a
        # second one.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True)
        inbound = self._serving_inbound("kazoj-nogur")
        created: list[str] = []

        def record_create(
            _env: object,
            _inbound_id: object,
            _client_id: str,
            email: str,
            _sub_id: str,
            _timeout: object,
        ) -> tuple[bool, str]:
            created.append(email)
            return True, "client created"

        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda env, port, timeout: inbound
        )
        monkeypatch.setattr("pyntara.xui.create_client", record_create)
        monkeypatch.setattr(
            "pyntara.xui.find_client", lambda env, _m, timeout: {"email": _m}
        )
        monkeypatch.setattr(
            "pyntara.xui.client_links",
            lambda env, email, timeout: ["vless://x@203.0.113.5:443"],
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound", lambda env, inbound, timeout: (True, "updated")
        )
        monkeypatch.setattr(
            xray_inbound,
            "_server_share_address",
            lambda inbound, facts: "203.0.113.5",
        )
        fake_kp = Mock()
        fake_kp.root_group = Mock()
        fake_kp.find_entries.return_value = None
        fake_kp.add_entry = Mock()
        fake_kp.save = Mock()
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda: fake_kp)
        ctx = _ctx(monkeypatch, tmp_path)
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
        assert created == []
        notes = next(
            call.kwargs["notes"]
            for call in fake_kp.add_entry.call_args_list
            if call.args[1] == "xray_connection"
        )
        assert "CLIENT_EMAIL=kazoj-nogur" in notes

    def test_warns_when_the_vault_is_empty_and_two_clients_are_served(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Two clients on the inbound and no identity in the vault: the first
        # identity is adopted and the extra client stays in the panel, which
        # the operator must hear about because the vault and the panel have
        # drifted apart.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True)
        inbound = self._serving_inbound("kazoj-nogur", "tapom-hovuj")
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda env, port, timeout: inbound
        )
        monkeypatch.setattr(
            "pyntara.xui.create_client",
            lambda env, inbound_id, client_id, email, sub_id, timeout: (True, "client created"),
        )
        monkeypatch.setattr(
            "pyntara.xui.find_client", lambda env, _m, timeout: {"email": _m}
        )
        monkeypatch.setattr(
            "pyntara.xui.client_links",
            lambda env, email, timeout: ["vless://x@203.0.113.5:443"],
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound", lambda env, inbound, timeout: (True, "updated")
        )
        monkeypatch.setattr(
            xray_inbound,
            "_server_share_address",
            lambda inbound, facts: "203.0.113.5",
        )
        fake_kp = Mock()
        fake_kp.root_group = Mock()
        fake_kp.find_entries.return_value = None
        fake_kp.add_entry = Mock()
        fake_kp.save = Mock()
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda: fake_kp)
        ctx = _ctx(monkeypatch, tmp_path)
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
        assert any("serves 2 clients" in warning for warning in result.warnings or ())

    def test_reports_warning_when_the_vault_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The runtime vault is unavailable: the client step still runs and
        # the task completes with a warning instead of failing.
        _stage2_fake(monkeypatch, tmp_path, inbound_exists=True, vault_ok=False)
        inbound = self._inbound()
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_port", lambda env, port, timeout: inbound
        )
        monkeypatch.setattr(
            "pyntara.xui.find_client", lambda env, email, timeout: {"email": "a-b"}
        )
        monkeypatch.setattr(
            "pyntara.xui.client_links",
            lambda env, email, timeout: ["vless://x@203.0.113.5:443"],
        )
        monkeypatch.setattr(
            "pyntara.xui.update_inbound", lambda env, inbound, timeout: (True, "updated")
        )
        monkeypatch.setattr(
            xray_inbound, "_server_share_address", lambda inbound, facts: "203.0.113.5"
        )
        monkeypatch.setattr("pyntara.metrics.open_runtime_vault", lambda: None)
        ctx = _ctx(monkeypatch, tmp_path)
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

    def test_returns_the_first_public_ipv4_of_the_run_facts(self) -> None:
        # The addresses are collected once per run, so the helper only
        # picks the first public IPv4: no further query is made.
        facts = _facts(public=("203.0.113.7", "203.0.113.8"))
        assert xray_facts._detect_server_ip(facts) == "203.0.113.7"

    def test_returns_none_when_only_ipv6_is_reported(self) -> None:
        # A Let's Encrypt IP certificate needs IPv4: an IPv6-only machine
        # reports no IPv4 address and keeps its self-signed certificate.
        facts = _facts(public_ipv6=("2001:db8::1",))
        assert xray_facts._detect_server_ip(facts) is None


class TestServerShareAddress:
    """Tests for the address priority used by the connection stage."""

    def _inbound(self, share_addr: str = "") -> dict[str, object]:
        return {"shareAddr": share_addr}

    def _point_the_mesh_address_at_a_missing_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Point the mesh address file at a path that does not exist.

        The address file is a declared value, so the test points the value
        at a path inside the temporary directory and never reads the real
        node address of the machine it runs on.
        """

        monkeypatch.setattr(
            yggdrasil_values,
            "ADDRESS_FILE_PATH",
            tmp_path / "yggdrasil_self_address",
        )

    def test_prefers_a_public_address_that_belongs_to_the_machine(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A white address really sits on an interface: nothing else is
        # consulted, because the machine is reachable directly.
        facts = _facts(public=("203.0.113.5",), local=("203.0.113.5", "10.0.0.1"))
        assert (
            xray_facts._server_share_address(self._inbound(), facts)
            == "203.0.113.5"
        )

    def test_uses_the_mesh_address_for_a_foreign_public_address(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The reported address belongs to the provider NAT, not to this
        # machine, so a mesh address beats a private one.
        facts = _facts(public=("190.55.165.52",), local=("192.168.1.5",))
        address_file = tmp_path / "yggdrasil_self_address"
        address_file.write_text("2001:db8::9\n", encoding="utf-8")
        monkeypatch.setattr(yggdrasil_values, "ADDRESS_FILE_PATH", address_file)
        assert (
            xray_facts._server_share_address(self._inbound(), facts)
            == "[2001:db8::9]"
        )

    def test_uses_the_client_address_when_upnp_forwards_the_port(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # UPnP opened the port, so the address the forwarding returned is
        # the one a client can reach.
        facts = _facts(
            public=("190.55.165.52",),
            local=("192.168.1.5",),
            router="190.55.165.52",
            client="190.55.165.52",
        )
        self._point_the_mesh_address_at_a_missing_file(monkeypatch, tmp_path)
        assert (
            xray_facts._server_share_address(self._inbound(), facts)
            == "190.55.165.52"
        )

    def test_ignores_a_client_address_that_is_not_forwarded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The router answered but the mapping is not in place: the run
        # facts carry no client address, so the local address is used.
        facts = _facts(public=("190.55.165.52",), local=("192.168.1.5",))
        self._point_the_mesh_address_at_a_missing_file(monkeypatch, tmp_path)
        assert (
            xray_facts._server_share_address(self._inbound(), facts)
            == "192.168.1.5"
        )

    def test_uses_the_local_address_when_nothing_else_answers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # No white address, no UPnP, no yggdrasil: the server still works
        # for the local network instead of writing no address at all.
        facts = _facts(local=("192.168.1.5",))
        self._point_the_mesh_address_at_a_missing_file(monkeypatch, tmp_path)
        assert (
            xray_facts._server_share_address(self._inbound(), facts)
            == "192.168.1.5"
        )

    def test_keeps_the_share_address_stored_in_the_panel(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._point_the_mesh_address_at_a_missing_file(monkeypatch, tmp_path)
        assert (
            xray_facts._server_share_address(
                self._inbound("198.51.100.9"),
                _facts(),
            )
            == "198.51.100.9"
        )

    def test_returns_none_without_any_source(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._point_the_mesh_address_at_a_missing_file(monkeypatch, tmp_path)
        assert (
            xray_facts._server_share_address(
                self._inbound(), _facts()
            )
            is None
        )

    def test_machine_address_returns_the_address_on_an_interface(self) -> None:
        # The reported address that also sits on an interface belongs to
        # this machine: the machine is reachable without a forward.
        assert (
            xray_facts._machine_public_address(
                _addresses(ipv4=("190.55.165.52",), ipv6=("2001:db8::1",)),
                ("192.168.1.5", "2001:db8::1"),
            )
            == "2001:db8::1"
        )

    def test_machine_address_is_none_for_a_foreign_address(self) -> None:
        # Every reported address belongs to a provider or a router: a NAT
        # sits in front and a forward is needed.
        assert (
            xray_facts._machine_public_address(
                _addresses(ipv4=("190.55.165.52",)), ("192.168.1.5",)
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

        monkeypatch.setattr(xray_facts, "package_is_installed", lambda package, timeout: True)
        monkeypatch.setattr(xray_facts, "install_package_once", fail_install)
        assert xray_facts._ensure_upnp_client(30.0) is True

    def test_installs_the_configured_package_through_the_shared_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed: list[str] = []

        def fake_install(package: str, _timeout: float) -> tuple[bool, str]:
            installed.append(package)
            return (True, "")

        monkeypatch.setattr(xray_facts, "package_is_installed", lambda package, timeout: False)
        monkeypatch.setattr(xray_facts, "install_package_once", fake_install)
        assert xray_facts._ensure_upnp_client(30.0) is True
        assert installed == ["miniupnpc"]

    def test_reports_failure_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(xray_facts, "package_is_installed", lambda package, timeout: False)
        monkeypatch.setattr(
            xray_facts,
            "install_package_once",
            lambda package, timeout: (False, "no candidate"),
        )
        assert xray_facts._ensure_upnp_client(30.0) is False


class TestCollectRunFacts:
    """Tests for the once-per-run address and router collection."""

    def test_collects_addresses_and_asks_the_router_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # One query per source: the stages read the facts instead of
        # asking again, which keeps a run short.
        router_calls: list[float] = []
        monkeypatch.setattr(
            xray_facts,
            "_public_addresses",
            lambda timeout: _addresses(ipv4=("203.0.113.5",)),
        )
        monkeypatch.setattr(xray_facts, "local_addresses", lambda timeout: ("10.0.0.1",))
        monkeypatch.setattr(xray_facts, "_ensure_upnp_client", lambda timeout: True)

        def fake_router(command: str, timeout: float) -> str:
            del command
            router_calls.append(timeout)
            return "190.55.165.52"

        monkeypatch.setattr("pyntara.upnp.router_external_address", fake_router)
        facts = xray_facts._collect_run_facts(30.0)
        assert facts.public_addresses.ipv4 == ("203.0.113.5",)
        assert facts.local_addresses == ("10.0.0.1",)
        assert facts.router_address == "190.55.165.52"
        assert facts.client_address is None
        assert router_calls == [30.0]

    def test_does_not_ask_the_router_when_upnp_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The switch is a declared value, so a machine that must not talk
        # to its router never installs the client or asks for a mapping.
        monkeypatch.setattr(panel_values, "UPNP_ENABLED", 0)

        def fail_install(_timeout: float) -> bool:
            raise AssertionError("the UPnP client must not be installed")

        def fail_router(*args: object, **kwargs: object) -> str:  # pragma: no cover
            raise AssertionError("the router must not be asked")

        monkeypatch.setattr(
            xray_facts, "_public_addresses", lambda timeout: _addresses()
        )
        monkeypatch.setattr(xray_facts, "local_addresses", lambda timeout: ())
        monkeypatch.setattr(xray_facts, "_ensure_upnp_client", fail_install)
        monkeypatch.setattr("pyntara.upnp.router_external_address", fail_router)
        facts = xray_facts._collect_run_facts(30.0)
        assert facts.router_address is None

    def test_reports_no_router_when_the_client_cannot_be_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            xray_facts, "_public_addresses", lambda timeout: _addresses()
        )
        monkeypatch.setattr(xray_facts, "local_addresses", lambda timeout: ())
        monkeypatch.setattr(xray_facts, "_ensure_upnp_client", lambda timeout: False)
        facts = xray_facts._collect_run_facts(30.0)
        assert facts.router_address is None

    def test_skips_upnp_when_the_machine_owns_a_public_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The address an echo service reports also sits on an interface,
        # so the machine is reachable directly: the client package is not
        # installed and the router is never asked.
        def fail_install(_timeout: float) -> bool:
            raise AssertionError("the UPnP client must not be installed")

        def fail_router(*args: object, **kwargs: object) -> str:
            raise AssertionError("the router must not be asked")

        monkeypatch.setattr(
            xray_facts,
            "_public_addresses",
            lambda timeout: _addresses(ipv4=("203.0.113.5",)),
        )
        monkeypatch.setattr(xray_facts, "local_addresses", lambda timeout: ("203.0.113.5",))
        monkeypatch.setattr(xray_facts, "_ensure_upnp_client", fail_install)
        monkeypatch.setattr("pyntara.upnp.router_external_address", fail_router)
        facts = xray_facts._collect_run_facts(30.0)
        assert facts.router_address is None


class TestForwardUpnpPorts:
    """Tests for the port forwarding the task asks the router for."""

    def test_forwards_the_client_port_and_the_acme_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Both ports are forwarded through the router address already
        # read for this run, so the router is never asked twice.
        calls: list[tuple[object, ...]] = []

        def fake_forward(*args: object, **kwargs: object) -> ForwardedAddress:
            calls.append(args)
            return ForwardedAddress("190.55.165.52", True)

        monkeypatch.setattr("pyntara.upnp.forward_inbound_port", fake_forward)
        monkeypatch.setattr(xray_facts.socket, "gethostname", lambda: "testhost")
        facts = _facts(public=("190.55.165.52",), router="190.55.165.52")
        assert xray_facts._forward_upnp_ports(facts, 30.0) == "190.55.165.52"
        # The description is the ownership mark of the rule and carries the
        # machine name, so a neighbour of this project on the same router
        # keeps its own rule.
        assert calls[0][0:4] == (
            "upnpc",
            "pyntara xray testhost",
            panel_values.INBOUND_PORT,
            panel_values.UPNP_PROTOCOL,
        )
        assert calls[0][4] == ("190.55.165.52",)
        assert calls[0][6] == "190.55.165.52"
        assert calls[1][0:4] == (
            "upnpc",
            "pyntara xray testhost",
            panel_values.ACME_PORT,
            panel_values.UPNP_PROTOCOL,
        )
        assert calls[1][6] == "190.55.165.52"

    def test_skips_the_acme_port_when_ssl_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[object, ...]] = []

        def fake_forward(*args: object, **kwargs: object) -> ForwardedAddress:
            calls.append(args)
            return ForwardedAddress("190.55.165.52", True)

        monkeypatch.setattr("pyntara.upnp.forward_inbound_port", fake_forward)
        monkeypatch.setattr(panel_values, "SSL_ENABLED", 0)
        facts = _facts(router="190.55.165.52")
        xray_facts._forward_upnp_ports(facts, 30.0)
        assert [call[2] for call in calls] == [panel_values.INBOUND_PORT]

    def test_returns_no_client_address_behind_a_provider_nat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The port is forwarded, but the address belongs to the provider
        # network: a client outside it cannot connect, so the task keeps
        # its own address order and the journal carries the forwarded one.
        def fake_forward(*args: object, **kwargs: object) -> ForwardedAddress:
            return ForwardedAddress("100.64.0.7", False)

        monkeypatch.setattr("pyntara.upnp.forward_inbound_port", fake_forward)
        facts = _facts(public=("190.55.165.52",), router="100.64.0.7")
        assert xray_facts._forward_upnp_ports(facts, 30.0) is None

    def test_does_nothing_without_a_router(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No UPnP router on this network: no mapping is attempted at all.
        def fail_forward(*args: object, **kwargs: object) -> str:
            raise AssertionError("the mapping must not be attempted")

        monkeypatch.setattr("pyntara.upnp.forward_inbound_port", fail_forward)
        assert xray_facts._forward_upnp_ports(_facts(), 30.0) is None


# The vless link a test machine is a client of; the address is a
# documentation address, so no test can reach a real server.
PROFILE_LINK = (
    "vless://client-id@203.0.113.9:443?fp=chrome&pbk=PUBLICKEY"
    "&security=reality&sid=6ba85179e30d4fc2&sni=www.google.com"
    "&spx=%2Fspider&type=tcp#test"
)


def _source_vault(link: str = PROFILE_LINK) -> Mock:
    """A source vault whose profile entry carries the given link."""

    entry = Mock()
    entry.url = link
    kp = Mock()
    kp.root_group = Mock()
    kp.find_entries.return_value = entry
    return kp


def _profile_source(monkeypatch: pytest.MonkeyPatch, link: str = PROFILE_LINK) -> None:
    """Make the source vault helper answer with a vault holding the link."""

    monkeypatch.setattr(
        "pyntara.xray_local_proxy.open_source_vault",
        lambda repo_root, production, default, password: (
            _source_vault(link),
            Path("/repo/secrets/production.vault"),
        ),
    )


def _panel_env_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the panel environment read without a panel on the machine."""

    monkeypatch.setattr(
        "pyntara.xui.panel_environment",
        lambda timeout: {
            "XUI_API_TOKEN": "tok123",
            "XUI_PANEL_PORT": "3579",
            "XUI_SCHEME": "https",
        },
    )


def _template_settings() -> dict[str, object]:
    """An Xray template as the panel ships it, with its own restrictions."""

    return {
        "log": {"loglevel": "warning"},
        "outbounds": [
            {
                "tag": "direct",
                "protocol": "freedom",
                "settings": {
                    "finalRules": [{"outboundTag": "blocked", "ip": ["geoip:private"]}]
                },
            },
            {"tag": "blocked", "protocol": "blackhole", "settings": {}},
        ],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
                {"type": "field", "protocol": ["bittorrent"], "outboundTag": "blocked"},
            ],
            "domainStrategy": "AsIs",
        },
    }


class TestLocalProxyStage:
    """Tests for stage 6, the local proxy inbound of the panel."""

    def test_the_traffic_counter_keys_come_from_the_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The panel counts traffic into the two counter fields of the
        # declared field map, so a renamed pair there is what the
        # comparison leaves out; a field the map does not mark as a
        # counter is compared like any other.
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            panel_values,
            "XRAY_FIELD_KEYS",
            {
                **panel_values.XRAY_FIELD_KEYS,
                "up": "upload",
                "down": "download",
            },
        )
        assert (
            xray_client._inbound_matches(
                {"tag": "t", "upload": 0, "download": 0},
                {"tag": "t", "upload": 10, "download": 20},
            )
            is True
        )
        assert (
            xray_client._inbound_matches(
                {"tag": "t", "up": 0}, {"tag": "t", "up": 10}
            )
            is False
        )
        assert (
            xray_client._inbound_matches(
                {"tag": "t", "upload": 0, "download": 0},
                {"tag": "t", "upload": 0, "download": 0},
            )
            is True
        )

    def test_creates_the_inbound_when_the_tag_is_free(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _profile_source(monkeypatch)
        _panel_env_fake(monkeypatch)
        created: list[dict[str, object]] = []
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_tag", lambda env, tag, timeout: None
        )

        def fake_upsert(
            _env: object, payload: dict[str, object], _timeout: object
        ) -> tuple[bool, str]:
            created.append(payload)
            return True, "inbound added"

        monkeypatch.setattr("pyntara.xui.upsert_inbound", fake_upsert)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_local_proxy(30.0)
        assert result is not None
        assert result.changed is True
        assert created[0]["tag"] == "pyntara-local-proxy"
        assert created[0]["remark"] == "pyntara-local-proxy"
        assert created[0]["port"] == 10800
        assert created[0]["listen"] == "127.0.0.1"
        assert created[0]["protocol"] == "mixed"
        assert created[0]["total"] == 0
        assert created[0]["expiryTime"] == 0
        assert created[0]["sniffing"] == {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
            "metadataOnly": False,
            "routeOnly": False,
        }

    def test_does_nothing_when_the_inbound_already_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _profile_source(monkeypatch)
        _panel_env_fake(monkeypatch)
        stored = {
            "id": 2,
            "tag": "pyntara-local-proxy",
            "remark": "pyntara-local-proxy",
            "listen": "127.0.0.1",
            "port": 10800,
            "protocol": "mixed",
            "enable": True,
            "expiryTime": 0,
            "total": 0,
            "up": 4096,
            "down": 8192,
            "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
            "sniffing": {
                "enabled": True,
                "destOverride": ["http", "tls", "quic"],
                "metadataOnly": False,
                "routeOnly": False,
            },
        }
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_tag", lambda env, tag, timeout: stored
        )

        def fail_upsert(*args: object, **kwargs: object) -> object:
            raise AssertionError("the inbound must not be written again")

        monkeypatch.setattr("pyntara.xui.upsert_inbound", fail_upsert)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        assert xui._stage_local_proxy(30.0) is None

    def test_force_writes_the_inbound_that_already_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The stored definition matches and the running core disagrees with
        # it, which the comparison cannot see: force writes the inbound
        # again, and that is the only lever an operator has there.
        _profile_source(monkeypatch)
        _panel_env_fake(monkeypatch)
        stored = {
            "id": 2,
            "tag": "pyntara-local-proxy",
            "remark": "pyntara-local-proxy",
            "listen": "127.0.0.1",
            "port": 10800,
            "protocol": "mixed",
            "enable": True,
            "expiryTime": 0,
            "total": 0,
            "up": 4096,
            "down": 8192,
            "settings": {"auth": "noauth", "udp": True, "ip": "127.0.0.1"},
            "sniffing": {
                "enabled": True,
                "destOverride": ["http", "tls", "quic"],
                "metadataOnly": False,
                "routeOnly": False,
            },
        }
        written: list[dict[str, object]] = []
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_tag", lambda env, tag, timeout: stored
        )

        def record_upsert(
            _env: object, payload: dict[str, object], _timeout: object
        ) -> tuple[bool, str]:
            written.append(payload)
            return True, "inbound updated"

        monkeypatch.setattr("pyntara.xui.upsert_inbound", record_upsert)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_local_proxy(30.0, force=True)
        assert result is not None
        assert result.changed is True
        assert written and written[0]["tag"] == "pyntara-local-proxy"

    def test_replaces_the_inbound_when_the_definition_differs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _profile_source(monkeypatch)
        _panel_env_fake(monkeypatch)
        stored = {
            "id": 2,
            "tag": "pyntara-local-proxy",
            "remark": "pyntara-local-proxy",
            "listen": "0.0.0.0",
            "port": 10801,
            "protocol": "mixed",
        }
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_tag", lambda env, tag, timeout: stored
        )
        written: list[dict[str, object]] = []

        def fake_upsert(
            _env: object, payload: dict[str, object], _timeout: object
        ) -> tuple[bool, str]:
            written.append(payload)
            return True, "inbound pyntara-local-proxy updated: updated"

        monkeypatch.setattr("pyntara.xui.upsert_inbound", fake_upsert)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_local_proxy(30.0)
        assert result is not None
        assert result.changed is True
        assert written[0]["port"] == 10800
        assert written[0]["listen"] == "127.0.0.1"

    def test_the_local_proxy_needs_no_profile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The client half is built on every machine, so a vault without a
        # client profile is not a reason to leave the machine without its
        # local proxy: the pool of that proxy is filled elsewhere.
        monkeypatch.setattr(
            "pyntara.xray_local_proxy.open_source_vault",
            lambda repo_root, production, default, password: None,
        )
        _panel_env_fake(monkeypatch)
        created: list[dict[str, object]] = []
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_tag", lambda env, tag, timeout: None
        )

        def fake_upsert(
            _env: object, payload: dict[str, object], _timeout: object
        ) -> tuple[bool, str]:
            created.append(payload)
            return True, "inbound added"

        monkeypatch.setattr("pyntara.xui.upsert_inbound", fake_upsert)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_local_proxy(30.0)
        assert result is not None
        assert result.changed is True
        assert created

    def test_the_remote_server_gets_its_local_proxy_too(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The machine other clients connect to is not left without its own
        # client half: only the connection to itself is left out, which is
        # the business of stage 7.
        _profile_source(monkeypatch)
        _panel_env_fake(monkeypatch)
        created: list[dict[str, object]] = []
        monkeypatch.setattr(
            "pyntara.xui.find_inbound_by_tag", lambda env, tag, timeout: None
        )

        def fake_upsert(
            _env: object, payload: dict[str, object], _timeout: object
        ) -> tuple[bool, str]:
            created.append(payload)
            return True, "inbound added"

        monkeypatch.setattr("pyntara.xui.upsert_inbound", fake_upsert)
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_local_proxy(30.0)
        assert result is not None
        assert result.changed is True
        assert created


class TestRoutingPolicyStage:
    """Tests for stage 7, the routing policy of the local proxy."""

    def _prepare(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        in_country: bool = False,
        settings: dict[str, object] | None = None,
        rejected: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """Prepare the stage: patch the panel, the country and the writes."""

        _profile_source(monkeypatch)
        _panel_env_fake(monkeypatch)
        monkeypatch.setattr(
            xray_client,
            "directly_connected_networks",
            lambda timeout: ("10.10.0.0/24",),
        )
        monkeypatch.setattr(
            xray_client,
            "detect_country",
            lambda *_args, **_kwargs: CountryReport(
                answers=(),
                values=(),
                matched_word="russia" if in_country else None,
            ),
        )
        monkeypatch.setattr(
            "pyntara.xui.validate_geodata_tokens",
            lambda env, kind, tokens, timeout: {
                token: reason
                for token, reason in (rejected or {}).items()
                if token in tokens
            },
        )
        template = xui_client.XrayTemplate(
            settings=settings if settings is not None else _template_settings(),
            outbound_test_url="https://www.google.com/generate_204",
        )
        monkeypatch.setattr(
            "pyntara.xui.read_xray_template", lambda env, timeout: template
        )
        writes: list[dict[str, object]] = []

        def fake_write(
            _env: object,
            wanted: xui_client.XrayTemplate,
            _timeout: object,
        ) -> tuple[bool, str]:
            writes.append(wanted.settings)
            return True, "applied"

        monkeypatch.setattr("pyntara.xui.write_xray_template", fake_write)
        return writes

    def _route_fake(self, expected: dict[str, str], seen: list[str]) -> object:
        """Answer the routing checks, refusing a destination not expected."""

        def fake(_env: object, **kwargs: object) -> tuple[bool, str]:
            destination = kwargs.get("domain") or kwargs.get("address")
            seen.append(str(destination))
            if not isinstance(destination, str) or destination not in expected:
                raise AssertionError(f"unexpected routing check for {destination}")
            return True, expected[destination]

        return fake

    def _rules(self, settings: dict[str, object]) -> list[dict[str, object]]:
        routing = settings["routing"]
        assert isinstance(routing, dict)
        rules = routing["rules"]
        assert isinstance(rules, list)
        return cast("list[dict[str, object]]", rules)

    def test_route_test_port_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The port every routing check knocks on is a declared value:
        # another port in the section is the port the running core is asked
        # about, for a domain check and for an address check alike.
        monkeypatch.setattr(panel_values, "ROUTE_TEST_PORT", 8443)
        ports: list[object] = []

        def fake_route(
            _env: object, **kwargs: object
        ) -> tuple[bool, str]:
            ports.append(kwargs.get("port"))
            return True, "direct"

        monkeypatch.setattr("pyntara.xui.route_test", fake_route)
        monkeypatch.setattr(
            xray_client,
            "_route_expectations",
            lambda _policy, **_kwargs: (
                ("example.com", "domain", "direct"),
                ("10.10.0.0", "address", "direct"),
            ),
        )
        policy = cast(
            "routing_policy.LocalProxyPolicy",
            SimpleNamespace(inbound_tag="pyntara-local-proxy"),
        )
        failures = xray_client._verify_routes({}, 30.0, policy)
        assert failures == ((), None)
        assert ports == [8443, 8443]

    def test_route_test_words_come_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The network and the protocol the check declares for the
        # destination are declared values: another pair of them is what the
        # running core is asked about, for a domain check and for an
        # address check alike.
        monkeypatch.setattr(panel_values, "ROUTE_TEST_NETWORK", "my-tcp")
        monkeypatch.setattr(panel_values, "ROUTE_TEST_PROTOCOL", "my-tls")
        words: list[tuple[object, object]] = []

        def fake_route(
            _env: object, **kwargs: object
        ) -> tuple[bool, str]:
            words.append((kwargs.get("network"), kwargs.get("protocol")))
            return True, "direct"

        monkeypatch.setattr("pyntara.xui.route_test", fake_route)
        monkeypatch.setattr(
            xray_client,
            "_route_expectations",
            lambda _policy, **_kwargs: (
                ("example.com", "domain", "direct"),
                ("10.10.0.0", "address", "direct"),
            ),
        )
        policy = cast(
            "routing_policy.LocalProxyPolicy",
            SimpleNamespace(inbound_tag="pyntara-local-proxy"),
        )
        assert xray_client._verify_routes({}, 30.0, policy) == ((), None)
        assert words == [("my-tcp", "my-tls"), ("my-tcp", "my-tls")]

    def _expected_outbounds(self) -> dict[str, str]:
        """The decision every destination class of this test machine gets."""

        return {
            "pyntara-check.onion": "pyntara-tor",
            "pyntara-check.i2p": "pyntara-i2p",
            "doubleclick.net": "blocked",
            "localhost": "direct",
            "10.10.0.0": "direct",
            "example.com": "pyntara-remote",
        }

    def test_waits_for_a_core_that_has_not_finished_starting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A written template is reconciled with the running core, and the
        # panel answers the write before a restart it triggered has
        # finished. The first round of questions therefore lands in that
        # window and gets no decision; the stage asks again instead of
        # reading the boot as a wrong policy, and it does not write the
        # template a second time for it.
        writes = self._prepare(monkeypatch, tmp_path)
        expected = self._expected_outbounds()
        answers = {"count": 0}

        def fake_route(
            _env: object, **kwargs: object
        ) -> tuple[bool | None, str]:
            answers["count"] += 1
            if answers["count"] <= len(expected):
                return None, (
                    "rpc error: dial tcp 127.0.0.1:62789: connect: connection refused"
                )
            destination = kwargs.get("domain") or kwargs.get("address")
            return True, expected[cast(str, destination)]

        monkeypatch.setattr("pyntara.xui.route_test", fake_route)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "203.0.113.9\n"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert result.changed is True
        assert not result.warnings
        assert len(writes) == 1
        assert answers["count"] == 2 * len(expected)

    def test_the_core_wait_budget_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The budget the core gets to answer is a declared value: zero
        # means one round and no pause, so a machine that wants an
        # immediate answer declares it and gets a warning instead of a
        # wait.
        self._prepare(monkeypatch, tmp_path)
        context = _ctx(monkeypatch, tmp_path)
        monkeypatch.setattr(panel_values, "CORE_READY_WAIT_SECONDS", 0)
        rounds = {"count": 0}

        def fake_route(
            _env: object, **_kwargs: object
        ) -> tuple[bool | None, str]:
            rounds["count"] += 1
            return None, "the panel was unreachable"

        sleeps: list[float] = []
        monkeypatch.setattr("pyntara.xui.route_test", fake_route)
        monkeypatch.setattr(xray_client.time, "sleep", sleeps.append)
        monkeypatch.setattr(
            "pyntara.xui.core_diagnostics",
            lambda env, timeout: "the panel reports its core stopped",
        )
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("the proxy path must not be asked")
            ),
        )
        result = xui._stage_routing_policy(context, 30.0, _facts())
        assert result is not None
        assert rounds["count"] == 1
        assert sleeps == []
        assert result.warnings is not None
        assert len(result.warnings) == 1
        assert "did not answer within 0 s" in result.warnings[0]
        assert "the panel was unreachable" in result.warnings[0]
        assert "the panel reports its core stopped" in result.warnings[0]

    def test_the_core_wait_pause_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The budget and the pause between two rounds are declared values:
        # the stage waits exactly the declared pause, and a tiny pair of
        # them is what keeps the test from waiting and still proves that
        # the numbers come from the values.
        self._prepare(monkeypatch, tmp_path)
        context = _ctx(monkeypatch, tmp_path)
        monkeypatch.setattr(panel_values, "CORE_READY_WAIT_SECONDS", 0.05)
        monkeypatch.setattr(panel_values, "READINESS_CHECK_DELAY_SECONDS", 0.05)
        sleeps: list[float] = []

        def fake_route(
            _env: object, **_kwargs: object
        ) -> tuple[bool | None, str]:
            return None, "the panel was unreachable"

        monkeypatch.setattr("pyntara.xui.route_test", fake_route)
        monkeypatch.setattr(xray_client.time, "sleep", sleeps.append)
        monkeypatch.setattr(
            "pyntara.xui.core_diagnostics",
            lambda env, timeout: "the panel reports its core stopped",
        )
        result = xui._stage_routing_policy(context, 30.0, _facts())
        assert result is not None
        assert set(sleeps) == {0.05}
        assert "did not answer within 0.05 s" in cast(tuple[str, ...], result.warnings)[0]

    def test_a_disagreement_writes_the_template_again(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The running core can hold a rule set the stored template no
        # longer matches, so a decision that disagrees is answered by
        # writing the same template once more and asking again; the
        # disagreement that survives the second write is reported.
        writes = self._prepare(monkeypatch, tmp_path)
        wrong = dict.fromkeys(self._expected_outbounds(), "direct")

        def fake_route(
            _env: object, **kwargs: object
        ) -> tuple[bool | None, str]:
            destination = kwargs.get("domain") or kwargs.get("address")
            return True, wrong[cast(str, destination)]

        monkeypatch.setattr("pyntara.xui.route_test", fake_route)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "203.0.113.9\n"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert result.changed is True
        assert len(writes) == 2
        assert result.warnings is not None
        assert any(
            "pyntara-check.onion: expected pyntara-tor, got direct" in warning
            for warning in result.warnings
        )

    def test_applies_the_policy_of_a_machine_outside_russia(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        writes = self._prepare(monkeypatch, tmp_path)
        seen: list[str] = []
        monkeypatch.setattr(
            "pyntara.xui.route_test",
            self._route_fake(
                {
                    "pyntara-check.onion": "pyntara-tor",
                    "pyntara-check.i2p": "pyntara-i2p",
                    "doubleclick.net": "blocked",
                    "localhost": "direct",
                    "10.10.0.0": "direct",
                    "example.com": "pyntara-remote",
                },
                seen,
            ),
        )
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "203.0.113.9\n"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert result.changed is True
        assert not result.warnings
        assert sorted(seen) == [
            "10.10.0.0",
            "doubleclick.net",
            "example.com",
            "localhost",
            "pyntara-check.i2p",
            "pyntara-check.onion",
        ]
        rules = self._rules(writes[0])
        assert rules[0] == {
            "type": "field",
            "inboundTag": ["api"],
            "outboundTag": "api",
        }
        assert rules[1] == {
            "type": "field",
            "inboundTag": ["pyntara-local-proxy"],
            "domain": ["geosite:category-ads-all"],
            "outboundTag": "blocked",
        }
        assert rules[-1] == {
            "type": "field",
            "inboundTag": ["pyntara-local-proxy"],
            "balancerTag": panel_values.POOL_BALANCER_TAG,
        }
        outbounds = cast("list[dict[str, object]]", writes[0]["outbounds"])
        assert [outbound["tag"] for outbound in outbounds] == [
            "direct",
            "blocked",
            "pyntara-remote",
            "pyntara-tor",
            "pyntara-i2p",
        ]
        routing = writes[0]["routing"]
        assert isinstance(routing, dict)
        assert routing["domainStrategy"] == "AsIs"
        assert routing["balancers"] == [
            {
                "tag": panel_values.POOL_BALANCER_TAG,
                "selector": [panel_values.POOL_MEMBER_PREFIX, panel_values.REMOTE_OUTBOUND_TAG],
                "strategy": {"type": "leastPing"},
                "fallbackTag": panel_values.REMOTE_OUTBOUND_TAG,
            }
        ]
        assert writes[0]["observatory"] == {
            "subjectSelector": [
                panel_values.POOL_MEMBER_PREFIX,
                panel_values.REMOTE_OUTBOUND_TAG,
            ],
            "probeUrl": panel_values.POOL_PROBE_URL,
            "probeInterval": panel_values.POOL_PROBE_INTERVAL,
            "enableConcurrency": panel_values.POOL_ENABLE_CONCURRENCY,
        }

    def test_applies_the_policy_of_a_machine_in_russia(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        writes = self._prepare(monkeypatch, tmp_path, in_country=True)
        seen: list[str] = []
        monkeypatch.setattr(
            "pyntara.xui.route_test",
            self._route_fake(
                {
                    "pyntara-check.onion": "pyntara-tor",
                    "pyntara-check.i2p": "pyntara-i2p",
                    "doubleclick.net": "blocked",
                    "localhost": "direct",
                    "10.10.0.0": "direct",
                    "example.com": "direct",
                    "instagram.com": "pyntara-remote",
                },
                seen,
            ),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            self._answers_by_url(
                {
                    panel_values.PROXY_CHECK_URL: [(0, "77.245.209.27\n200")],
                    panel_values.PROXY_CHECK_BLOCKED_URL: [(0, '{"error": "no key"}\n401')],
                }
            ),
        )
        result = xui._stage_routing_policy(
            _ctx(monkeypatch, tmp_path), 30.0, _facts(public=("77.245.209.27",))
        )
        assert result is not None
        assert not result.warnings
        assert "instagram.com" in seen
        rules = self._rules(writes[0])
        blocked = [
            rule
            for rule in rules
            if rule.get("balancerTag") == panel_values.POOL_BALANCER_TAG and rule.get("domain")
        ]
        assert len(blocked) == 2
        domains = [cast("list[str]", rule["domain"]) for rule in blocked]
        assert domains[0] == [
            "geosite:category-ai-!cn",
            "geosite:openai",
            "geosite:xai",
            "geosite:netflix",
            "geosite:spotify",
            "geosite:category-social-media-!cn",
        ]
        assert domains[1] == ["ext-site:geosite_RU.dat:ru-blocked-all"]
        direct_rules = [
            rule
            for rule in rules
            if rule.get("outboundTag") == "direct" and rule.get("domain")
        ]
        assert any(
            "ext-site:geosite_RU.dat:ru-available-only-inside"
            in cast("list[str]", rule["domain"])
            for rule in direct_rules
        )
        routing = writes[0]["routing"]
        assert isinstance(routing, dict)
        assert routing["domainStrategy"] == "IPIfNonMatch"
        assert rules[-1] == {
            "type": "field",
            "inboundTag": ["pyntara-local-proxy"],
            "outboundTag": "direct",
        }

    def test_drops_a_category_the_panel_does_not_know(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        writes = self._prepare(
            monkeypatch,
            tmp_path,
            rejected={"geosite:netflix": "categoryMissing"},
        )
        monkeypatch.setattr(
            "pyntara.xui.route_test",
            lambda _e, **kwargs: (True, "pyntara-remote"),
        )
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "203.0.113.9\n"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert any("geosite:netflix" in w for w in result.warnings or ())
        applied = json.dumps(writes[0])
        assert "geosite:netflix" not in applied

    def test_writes_again_when_the_core_kept_an_older_rule_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The first pass answers nothing for every check, the second pass
        # answers correctly: the template is written once more and the
        # disagreement disappears without a warning.
        writes = self._prepare(monkeypatch, tmp_path)
        expected = {
            "pyntara-check.onion": "pyntara-tor",
            "pyntara-check.i2p": "pyntara-i2p",
            "doubleclick.net": "blocked",
            "localhost": "direct",
            "10.10.0.0": "direct",
            "example.com": "pyntara-remote",
        }
        answers: list[tuple[bool, str]] = [(False, "")] * 6

        def fake_route(
            _env: object, **kwargs: object
        ) -> tuple[bool, str]:
            destination = kwargs.get("domain") or kwargs.get("address")
            if answers:
                return answers.pop(0)
            return True, expected[cast("str", destination)]

        monkeypatch.setattr("pyntara.xui.route_test", fake_route)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "203.0.113.9\n"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert len(writes) == 2
        assert not any("expected" in w for w in result.warnings or ())

    def test_reports_a_route_that_stays_wrong(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        writes = self._prepare(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "pyntara.xui.route_test",
            lambda _e, **kwargs: (True, "direct"),
        )
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "203.0.113.9\n"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert len(writes) == 2
        assert sum(1 for w in result.warnings or () if "expected" in w) == 4

    def test_warns_when_the_proxy_path_does_not_leave(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._prepare(monkeypatch, tmp_path)
        seen: list[str] = []
        monkeypatch.setattr(
            "pyntara.xui.route_test",
            self._route_fake(
                {
                    "pyntara-check.onion": "pyntara-tor",
                    "pyntara-check.i2p": "pyntara-i2p",
                    "doubleclick.net": "blocked",
                    "localhost": "direct",
                    "10.10.0.0": "direct",
                    "example.com": "pyntara-remote",
                },
                seen,
            ),
        )
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "190.55.165.52\n"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        facts = _facts(local=("190.55.165.52",))
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, facts)
        assert result is not None
        assert any(
            "did not leave by the remote path" in w for w in result.warnings or ()
        )

    def _outside_russia_answers(self) -> dict[str, str]:
        """The core answers the policy expects outside Russia."""

        return {
            "pyntara-check.onion": "pyntara-tor",
            "pyntara-check.i2p": "pyntara-i2p",
            "doubleclick.net": "blocked",
            "localhost": "direct",
            "10.10.0.0": "direct",
            "example.com": "pyntara-remote",
        }

    def test_writes_again_when_the_proxy_path_failed_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Every routing check agrees while the path carries nothing, which
        # is the state a core that has not taken the pool leaves behind:
        # one more write is tried, and the second check passes without a
        # warning.
        writes = self._prepare(monkeypatch, tmp_path)
        seen: list[str] = []
        monkeypatch.setattr(
            "pyntara.xui.route_test",
            self._route_fake(self._outside_russia_answers(), seen),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            self._answers_by_url(
                {
                    panel_values.PROXY_CHECK_URL: [
                        (1, ""),
                        (1, ""),
                        (1, ""),
                        (0, "203.0.113.9\n200"),
                    ]
                }
            ),
        )
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert len(writes) == 2
        assert not [w for w in result.warnings or () if "answered nothing" in w]

    def test_the_proxy_path_retry_does_not_write_in_a_loop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The path stays broken, so the failure is reported and one more
        # write was tried; the retry must not turn into a run that writes
        # the template again and again.
        writes = self._prepare(monkeypatch, tmp_path)
        seen: list[str] = []
        monkeypatch.setattr(
            "pyntara.xui.route_test",
            self._route_fake(self._outside_russia_answers(), seen),
        )
        monkeypatch.setattr(
            xray_local_proxy, "run_command", lambda *a, **k: _FakeProc(1, "")
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert len(writes) == 2
        assert [w for w in result.warnings or () if "answered nothing" in w]

    def test_a_second_run_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The stage owns the policy and the pool, so a rerun finds both in
        # place: nothing is written and the stage reports no change.
        writes = self._prepare(monkeypatch, tmp_path)
        answer = {
            "pyntara-check.onion": "pyntara-tor",
            "pyntara-check.i2p": "pyntara-i2p",
            "doubleclick.net": "blocked",
            "localhost": "direct",
            "10.10.0.0": "direct",
            "example.com": "sota-sota-us-nyc-01",
        }
        seen: list[str] = []
        monkeypatch.setattr("pyntara.xui.route_test", self._route_fake(answer, seen))
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "203.0.113.9\n"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        first = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert first is not None
        assert first.changed is True
        assert writes
        seeded = writes[0]
        writes.clear()
        self._prepare(monkeypatch, tmp_path, settings=seeded)
        monkeypatch.setattr("pyntara.xui.route_test", self._route_fake(answer, []))
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "203.0.113.9\n"),
        )
        assert xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts()) is None
        assert writes == []

    def _pool_settings(self, tmp_path: Path) -> dict[str, object]:
        """A stored template as the sotavpn task leaves it: the pool is in."""

        fields = panel_values.XRAY_FIELD_KEYS
        values = panel_values.XRAY_VALUES
        updated, _ = routing_policy.apply_fastest_pool(
            _template_settings(),
            fields,
            balancer=routing_policy.build_balancer(
                fields,
                tag="pyntara-fastest",
                selector=("sota-", "pyntara-remote"),
                strategy=values["least_ping"],
                fallback_tag="pyntara-remote",
            ),
            observatory=routing_policy.build_observatory(
                fields,
                subject_selector=("sota-", "pyntara-remote"),
                probe_url="https://www.google.com/generate_204",
                probe_interval="30s",
                enable_concurrency=True,
            ),
        )
        return updated

    def test_a_pool_of_an_earlier_run_is_kept_and_members_are_accepted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A stored template may carry the pool of an earlier run with the
        # Sota nodes a subscription brought in. The remote classes must
        # keep naming that pool, and the core answers such a class with the
        # member it picked, so the check accepts a member the selector
        # covers instead of demanding the balancer tag itself.
        writes = self._prepare(
            monkeypatch, tmp_path, settings=self._pool_settings(tmp_path)
        )
        expected = self._expected_outbounds()
        expected["example.com"] = "sota-sota-us-nyc-01"
        seen: list[str] = []
        monkeypatch.setattr("pyntara.xui.route_test", self._route_fake(expected, seen))
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            self._answers_by_url({panel_values.PROXY_CHECK_URL: [(0, "198.51.100.20\n200")]}),
        )
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert result.changed is True
        assert not result.warnings
        assert len(writes) == 1
        routing = writes[0]["routing"]
        assert isinstance(routing, dict)
        assert routing["balancers"] == [
            {
                "tag": panel_values.POOL_BALANCER_TAG,
                "selector": [panel_values.POOL_MEMBER_PREFIX, panel_values.REMOTE_OUTBOUND_TAG],
                "strategy": {"type": "leastPing"},
                "fallbackTag": panel_values.REMOTE_OUTBOUND_TAG,
            }
        ]
        rules = self._rules(writes[0])
        assert not [
            rule for rule in rules if rule.get("outboundTag") == panel_values.REMOTE_OUTBOUND_TAG
        ]
        assert [
            rule for rule in rules if rule.get("balancerTag") == panel_values.POOL_BALANCER_TAG
        ]

    def test_a_remote_answer_outside_the_pool_is_reported_with_the_pool(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A pool accepts the members its selector covers and nothing else:
        # an answer from outside it is still a disagreement, and the
        # warning names the pool so the operator knows what was expected.
        self._prepare(monkeypatch, tmp_path, settings=self._pool_settings(tmp_path))
        expected = self._expected_outbounds()
        expected["example.com"] = "direct"
        seen: list[str] = []
        monkeypatch.setattr("pyntara.xui.route_test", self._route_fake(expected, seen))
        monkeypatch.setattr(
            xray_local_proxy, "run_command", lambda *a, **k: _FakeProc(7, "")
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert any(
            "expected pyntara-fastest or a member of its pool" in warning
            for warning in result.warnings or ()
        )

    def test_a_direct_answer_is_still_reported_when_a_pool_carries_the_traffic(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The pool accepts any exit that is not this machine, because the
        # member the balancer picks is not known in advance, but an answer
        # that is an address of this machine is the silent direct path the
        # check exists to catch.
        self._prepare(monkeypatch, tmp_path, settings=self._pool_settings(tmp_path))
        expected = self._expected_outbounds()
        expected["example.com"] = "sota-sota-us-nyc-01"
        seen: list[str] = []
        monkeypatch.setattr("pyntara.xui.route_test", self._route_fake(expected, seen))
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: _FakeProc(0, "190.55.165.52\n200"),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        facts = _facts(local=("190.55.165.52",))
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, facts)
        assert result is not None
        assert any(
            "did not leave by the remote path" in warning
            for warning in result.warnings or ()
        )

    def _profile(self) -> routing_policy.VlessProfile:
        profile = routing_policy.parse_vless_link(
            PROFILE_LINK,
            panel_values.REMOTE_LINK_DEFAULT_PORT,
            panel_values.XRAY_VALUES["vless"],
            panel_values.VLESS_LINK_QUERY_KEYS,
            panel_values.XRAY_VALUES,
        )
        assert profile is not None
        return profile

    def _answers_by_url(self, answers: dict[str, list[tuple[int, str]]]) -> object:
        """Answer each check URL from its own queue of curl results."""

        queues = {url: list(items) for url, items in answers.items()}

        def fake(command: list[str], **kwargs: object) -> object:
            del kwargs
            url = command[-1]
            queue = queues.get(url)
            if not queue:
                raise AssertionError(f"unexpected request for {url}")
            code, body = queue.pop(0) if len(queue) > 1 else queue[0]
            # curl prints the write-out marker without a trailing newline.
            return _FakeProc(code, body.rstrip("\n"))

        return fake

    def test_accepts_the_direct_egress_of_a_machine_in_russia(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # In Russia the policy sends an ordinary foreign name directly, so
        # the address of this machine is the expected answer and the remote
        # path is proven separately by a URL that must use the tunnel.
        self._prepare(monkeypatch, tmp_path, in_country=True)
        monkeypatch.setattr(
            "pyntara.xui.route_test", lambda _e, **kwargs: (True, "direct")
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            self._answers_by_url(
                {
                    panel_values.PROXY_CHECK_URL: [(0, "77.245.209.27\n200")],
                    panel_values.PROXY_CHECK_BLOCKED_URL: [(0, '{"error": "no key"}\n401')],
                }
            ),
        )
        result = xui._stage_routing_policy(
            _ctx(monkeypatch, tmp_path), 30.0, _facts(public=("77.245.209.27",))
        )
        assert result is not None
        assert not [
            w for w in result.warnings or () if "proxy" in w or "remote path" in w
        ]

    def test_the_proxy_check_attempts_come_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # How many times a path check repeats its request before it reports
        # no answer is a declared value: a slow link may need more than one
        # attempt, and every attempt is a real request through the tunnel.
        self._prepare(monkeypatch, tmp_path)
        monkeypatch.setattr(panel_values, "PROXY_CHECK_ATTEMPTS", 4)
        calls: list[str] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(command[-1])
            return _FakeProc(7, "")

        monkeypatch.setattr(xray_local_proxy, "run_command", fake_run)
        policy = cast(
            "routing_policy.LocalProxyPolicy",
            SimpleNamespace(in_russia=False),
        )
        profile = cast(
            "routing_policy.VlessProfile",
            SimpleNamespace(address="203.0.113.9"),
        )
        warnings = xray_local_proxy._check_proxy_path(policy, profile, _facts())
        assert calls == [panel_values.PROXY_CHECK_URL] * 4
        assert warnings
        assert "in 4 attempts" in warnings[0]

    def test_reports_a_machine_in_russia_that_uses_the_server_for_the_plain_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The plain URL went through the remote server although the policy
        # routes it directly: the policy is not in place on this machine.
        self._prepare(monkeypatch, tmp_path, in_country=True)
        monkeypatch.setattr(
            "pyntara.xui.route_test", lambda _e, **kwargs: (True, "direct")
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            self._answers_by_url(
                {
                    panel_values.PROXY_CHECK_URL: [(0, "203.0.113.9\n200")],
                    panel_values.PROXY_CHECK_BLOCKED_URL: [(0, "ok\n200")],
                }
            ),
        )
        result = xui._stage_routing_policy(
            _ctx(monkeypatch, tmp_path), 30.0, _facts(public=("77.245.209.27",))
        )
        assert result is not None
        assert any("policy may not be in place" in w for w in result.warnings or ())

    def test_reports_a_proxied_url_that_never_answers_in_russia(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._prepare(monkeypatch, tmp_path, in_country=True)
        monkeypatch.setattr(
            "pyntara.xui.route_test", lambda _e, **kwargs: (True, "direct")
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            self._answers_by_url(
                {
                    panel_values.PROXY_CHECK_URL: [(0, "77.245.209.27\n200")],
                    panel_values.PROXY_CHECK_BLOCKED_URL: [(28, "")],
                }
            ),
        )
        result = xui._stage_routing_policy(
            _ctx(monkeypatch, tmp_path), 30.0, _facts(public=("77.245.209.27",))
        )
        assert result is not None
        warnings = [
            w for w in result.warnings or () if panel_values.PROXY_CHECK_BLOCKED_URL in w
        ]
        assert warnings
        assert "curl exit 28" in warnings[0]

    def test_the_no_answer_code_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The code curl prints when nothing answered is a declared value:
        # an answer carrying the declared code counts as no answer, while
        # the same answer is a status when the shipped code is declared.
        self._prepare(monkeypatch, tmp_path, in_country=True)
        monkeypatch.setattr(
            "pyntara.xui.route_test", lambda _e, **kwargs: (True, "direct")
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(panel_values, "TUNNEL_PROBE_NO_ANSWER_CODE", "NONE")
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            self._answers_by_url(
                {
                    panel_values.PROXY_CHECK_URL: [(0, "77.245.209.27\n200")],
                    panel_values.PROXY_CHECK_BLOCKED_URL: [(0, "body\nNONE")],
                }
            ),
        )
        result = xui._stage_routing_policy(
            _ctx(monkeypatch, tmp_path), 30.0, _facts(public=("77.245.209.27",))
        )
        assert result is not None
        warnings = [
            w for w in result.warnings or () if panel_values.PROXY_CHECK_BLOCKED_URL in w
        ]
        assert warnings
        assert "NONE" in warnings[0]

    def test_an_answer_with_the_shipped_code_is_a_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The same answer is a status and not a missing answer when the
        # shipped code is the one declared, so a working tunnel that prints
        # the shipped text is not reported.
        self._prepare(monkeypatch, tmp_path, in_country=True)
        monkeypatch.setattr(
            "pyntara.xui.route_test", lambda _e, **kwargs: (True, "direct")
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            self._answers_by_url(
                {
                    panel_values.PROXY_CHECK_URL: [(0, "77.245.209.27\n200")],
                    panel_values.PROXY_CHECK_BLOCKED_URL: [(0, "body\nNONE")],
                }
            ),
        )
        shipped = xui._stage_routing_policy(
            _ctx(monkeypatch, tmp_path), 30.0, _facts(public=("77.245.209.27",))
        )
        assert shipped is not None
        assert not [
            w
            for w in shipped.warnings or ()
            if panel_values.PROXY_CHECK_BLOCKED_URL in w
        ]

    def test_a_stalled_request_is_attempted_twice(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The first attempt stalls and the second answers: the check does
        # not raise an alarm for a single stall of a working tunnel.
        self._prepare(monkeypatch, tmp_path)
        attempts: list[str] = []
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "pyntara.xui.route_test",
            lambda _e, **kwargs: (True, "pyntara-remote"),
        )

        def fake(command: list[str], **kwargs: object) -> object:
            del kwargs
            url = command[-1]
            if url == panel_values.PROXY_CHECK_URL:
                attempts.append(url)
                if len(attempts) == 1:
                    return _FakeProc(28, "")
                return _FakeProc(0, "203.0.113.9\n200")
            raise AssertionError(f"unexpected request for {url}")

        monkeypatch.setattr(xray_local_proxy, "run_command", fake)
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, _facts())
        assert result is not None
        assert len(attempts) == 2
        assert not [w for w in result.warnings or () if "two attempts" in w]

    def _policy(self) -> routing_policy.LocalProxyPolicy:
        return routing_policy.LocalProxyPolicy(
            inbound_tag=panel_values.LOCAL_PROXY_TAG,
            remote_outbound_tag=panel_values.REMOTE_OUTBOUND_TAG,
            tor_outbound_tag=panel_values.TOR_OUTBOUND_TAG,
            i2p_outbound_tag=panel_values.I2P_OUTBOUND_TAG,
            direct_outbound_tag=panel_values.DIRECT_OUTBOUND_TAG,
            blocked_outbound_tag=panel_values.BLOCKED_OUTBOUND_TAG,
            tor_proxy_address=panel_values.TOR_PROXY_ADDRESS,
            i2p_proxy_address=panel_values.I2P_PROXY_ADDRESS,
            ad_block_domain_categories=panel_values.AD_BLOCK_DOMAIN_CATEGORIES,
            direct_domains=panel_values.DIRECT_DOMAINS,
            direct_ip_categories=panel_values.DIRECT_IP_CATEGORIES,
            direct_ip_networks=panel_values.DIRECT_IP_NETWORKS,
            own_networks=("10.10.0.0/24",),
            in_russia=False,
            russia_blocked_domain_categories=panel_values.RUSSIA_BLOCKED_DOMAIN_CATEGORIES,
            russia_blocked_ip_categories=panel_values.RUSSIA_BLOCKED_IP_CATEGORIES,
            russia_direct_domain_categories=panel_values.RUSSIA_DIRECT_DOMAIN_CATEGORIES,
            russia_direct_ip_categories=panel_values.RUSSIA_DIRECT_IP_CATEGORIES,
            geo_restricted_domain_categories=panel_values.GEO_RESTRICTED_DOMAIN_CATEGORIES,
            russia_domain_strategy=panel_values.RUSSIA_DOMAIN_STRATEGY,
            outside_russia_domain_strategy=panel_values.OUTSIDE_RUSSIA_DOMAIN_STRATEGY,
            panel_inbound_protocol=panel_values.PANEL_INBOUND_PROTOCOL,
            panel_blocked_rule_protocols=panel_values.PANEL_BLOCKED_RULE_PROTOCOLS,
            panel_private_block_category=panel_values.PANEL_PRIVATE_BLOCK_CATEGORY,
            field_keys=panel_values.XRAY_FIELD_KEYS,
            values=panel_values.XRAY_VALUES,
        )

    def test_the_machine_that_is_the_remote_server_gets_the_pool_without_itself(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The machine other clients connect to builds the same client half,
        # minus the connection to itself: its remote classes leave through
        # the pool of the subscriptions, the pool falls back to the direct
        # outbound, and no request through the local proxy is checked
        # against a remote path that does not exist there.
        _profile_source(monkeypatch)
        writes = self._prepare(monkeypatch, tmp_path)
        expected = self._expected_outbounds()
        expected["example.com"] = "direct"
        seen: list[str] = []
        monkeypatch.setattr("pyntara.xui.route_test", self._route_fake(expected, seen))
        monkeypatch.setattr(
            xray_local_proxy,
            "run_command",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("the proxy path must not be checked here")
            ),
        )
        _use_temporary_panel_paths(monkeypatch, tmp_path)
        facts = _facts(public=("203.0.113.9",))
        result = xui._stage_routing_policy(_ctx(monkeypatch, tmp_path), 30.0, facts)
        assert result is not None
        assert result.changed is True
        assert not result.warnings
        outbounds = cast("list[dict[str, object]]", writes[0]["outbounds"])
        assert [outbound["tag"] for outbound in outbounds] == [
            "direct",
            "blocked",
            "pyntara-tor",
            "pyntara-i2p",
        ]
        routing = writes[0]["routing"]
        assert isinstance(routing, dict)
        assert routing["balancers"] == [
            {
                "tag": panel_values.POOL_BALANCER_TAG,
                "selector": [panel_values.POOL_MEMBER_PREFIX],
                "strategy": {"type": "leastPing"},
                "fallbackTag": panel_values.DIRECT_OUTBOUND_TAG,
            }
        ]
        assert [
            rule
            for rule in self._rules(writes[0])
            if rule.get("balancerTag") == panel_values.POOL_BALANCER_TAG
        ]
