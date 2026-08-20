"""Unit tests for the dnscrypt_setup task.

All external resources (subprocess) are mocked via monkeypatch; the tests
only touch temporary fixtures (docs/guides/developer-guide.md). The
package is reported installed or absent through the shared FakeProc, the
proxy configuration file is a real file in a temporary directory and the
systemd and resolver commands are faked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.tasks import dnscrypt_setup as task_module

VERIFY_OK = "example.com has address 93.184.215.14\n"


def _ctx(tmp_path: Path, *, force: bool = False, manage_nm: bool = True):
    """Context with the task config rooted in the temporary directory."""

    return make_context(
        install_mode="server",
        force_tasks=frozenset({"dnscrypt_setup"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            dnscrypt_config_path=tmp_path / "etc" / "dnscrypt-proxy" / "dnscrypt-proxy.toml",
            dnscrypt_socket_dropin_dir=tmp_path
            / "etc"
            / "systemd"
            / "system"
            / "dnscrypt-proxy.socket.d",
            dnscrypt_resolved_conf_dir=tmp_path / "etc" / "systemd" / "resolved.conf.d",
            dnscrypt_fallback_resolvers=("1.1.1.1", "8.8.8.8"),
            dnscrypt_manage_networkmanager=manage_nm,
        ),
    )


def _install_proxy_config(tmp_path: Path) -> None:
    """Create the proxy configuration file the package ships.

    The file mirrors the shipped Ubuntu config: root keys, then sections.
    The task must add fallback_resolvers to the root table without
    touching the sections.
    """

    path = tmp_path / "etc" / "dnscrypt-proxy" / "dnscrypt-proxy.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# Empty listen_addresses to use systemd socket activation\n"
        "listen_addresses = []\n"
        "server_names = ['cloudflare']\n"
        "\n"
        "[query_log]\n"
        "  file = '/var/log/dnscrypt-proxy/query.log'\n"
        "\n"
        "[sources]\n"
        "  [sources.'public-resolvers']\n"
        "  url = 'https://download.dnscrypt.info/resolvers-list/v2/public-resolvers.md'\n",
        encoding="utf-8",
    )


def _install_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    package_installed: bool = False,
    service_active: bool = True,
    verify_ok: bool = True,
) -> list[list[str]]:
    """Replace run_command with a recorder; return the recorded calls.

    The package helpers are replaced directly, because install_package_once
    and package_is_installed call run_command from pyntara.utils, not from
    the task module, so patching the task module run_command alone would
    let the real apt-get run.
    """

    calls: list[list[str]] = []
    nm_connections = ["Wired", "Wifi"]

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "systemctl":
            if command[1] == "is-enabled":
                return _FakeProc(0, "enabled\n")
            if command[1] == "is-active":
                return _FakeProc(
                    0 if service_active else 1,
                    "active\n" if service_active else "inactive\n",
                )
            return _FakeProc(0)
        if command[0] == "nmcli":
            if command[1] == "--version":
                return _FakeProc(0, "")
            if command[1] == "-t":
                return _FakeProc(0, "\n".join(nm_connections) + "\n")
            return _FakeProc(0)
        if command[0] == "resolvectl":
            if verify_ok:
                return _FakeProc(0, VERIFY_OK)
            return _FakeProc(1, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.tasks.dnscrypt_setup.run_command", fake_run)
    monkeypatch.setattr(
        "pyntara.tasks.dnscrypt_setup.package_is_installed",
        lambda _pkg, _timeout: package_installed,
    )
    monkeypatch.setattr(
        "pyntara.tasks.dnscrypt_setup.install_package_once",
        lambda _pkg, _timeout: (True, ""),
    )
    monkeypatch.setattr(
        "pyntara.tasks.dnscrypt_setup.service_is_enabled",
        lambda _name, _timeout: True,
    )
    monkeypatch.setattr(
        "pyntara.tasks.dnscrypt_setup.service_is_active",
        lambda _name, _timeout: service_active,
    )
    return calls


def test_installs_and_configures_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The task installs the package, writes the socket drop-in, adds the
    # fallback resolvers to the proxy config, starts the service, writes
    # the resolved drop-in and verifies a real DNS query.
    _install_proxy_config(tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True

    socket_dropin = (
        tmp_path
        / "etc"
        / "systemd"
        / "system"
        / "dnscrypt-proxy.socket.d"
        / "pyntara.conf"
    )
    socket_content = socket_dropin.read_text(encoding="utf-8")
    assert "[Socket]" in socket_content
    assert "ListenStream=0.0.0.0:53053" in socket_content
    assert "ListenDatagram=0.0.0.0:53053" in socket_content

    proxy_config = (
        tmp_path / "etc" / "dnscrypt-proxy" / "dnscrypt-proxy.toml"
    ).read_text(encoding="utf-8")
    assert "fallback_resolvers = ['1.1.1.1', '8.8.8.8']" in proxy_config
    assert "[sources]" in proxy_config

    resolved_dropin = (
        tmp_path / "etc" / "systemd" / "resolved.conf.d" / "dnscrypt.conf"
    ).read_text(encoding="utf-8")
    assert "[Resolve]" in resolved_dropin
    assert "DNS=127.0.0.1:53053" in resolved_dropin
    assert "Domains=~." in resolved_dropin

    assert any(
        call[0] == "nmcli" and any("ignore-auto-dns" in arg for arg in call)
        for call in calls
    )
    assert any(call[0] == "resolvectl" for call in calls)


def test_skip_when_already_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An already-configured system with a working verification skips.
    _install_proxy_config(tmp_path)
    ctx = _ctx(tmp_path)
    cfg = ctx.config.dnscrypt_setup

    socket_dropin = cfg.socket_dropin_dir / cfg.socket_dropin_file_name
    socket_dropin.parent.mkdir(parents=True)
    socket_dropin.write_text(
        f"{cfg.socket_dropin_header}\n{cfg.socket_section}\n"
        "ListenStream=\nListenDatagram=\n"
        f"ListenStream={cfg.listen_address}\n"
        f"ListenDatagram={cfg.listen_address}\n",
        encoding="utf-8",
    )

    proxy_config = cfg.config_path
    proxy_config.write_text(
        "server_names = ['cloudflare']\n"
        "fallback_resolvers = ['1.1.1.1', '8.8.8.8']\n",
        encoding="utf-8",
    )

    resolved_dropin = cfg.resolved_conf_dir / cfg.dropin_file_name
    resolved_dropin.parent.mkdir(parents=True)
    resolved_dropin.write_text(
        f"{cfg.dropin_header}\n{cfg.resolve_section}\n"
        f"{cfg.dns_directive}\nDomains={cfg.domains_directive}\n",
        encoding="utf-8",
    )

    calls = _install_subprocess(
        monkeypatch, package_installed=True, service_active=True, verify_ok=True
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.skipped is True
    # No package install and no rewrite happen on a skip.
    assert not any(call[0] == "apt-get" for call in calls)


def test_failed_verification_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed DNS query makes the task report an error and leave the
    # system as is (no revert).
    _install_proxy_config(tmp_path)
    ctx = _ctx(tmp_path)
    _install_subprocess(monkeypatch, verify_ok=False)
    result = task_module.task(ctx)
    assert result.success is False
    assert result.changed is False
    assert "verification" in (result.error or "").lower()


def test_force_rewrites_even_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force mode rewrites the drop-ins and restarts the service even when
    # the target state is already reached.
    _install_proxy_config(tmp_path)
    ctx = _ctx(tmp_path, force=True)
    calls = _install_subprocess(
        monkeypatch, package_installed=True, service_active=True, verify_ok=True
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert any(call[0] == "systemctl" and "daemon-reload" in call for call in calls)
