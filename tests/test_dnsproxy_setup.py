from __future__ import annotations

from pathlib import Path
from typing import Any

from pykeepass import PyKeePass, create_database
from support import FakeProc, make_config, make_context

from pyntara.tasks import dnsproxy_setup as task_module

PASSWORD = "password"


def install_vault(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "secrets" / "production.vault"
    path.parent.mkdir()
    create_database(str(path), password=PASSWORD)
    vault = PyKeePass(str(path), password=PASSWORD)
    group = vault.add_group(vault.root_group, "NextDNS")
    vault.add_entry(group, "profile", "39284e", "")
    vault.save()
    monkeypatch.setattr("pyntara.tasks.local_vault_setup.REPO_ROOT", tmp_path)


def test_discover_dns_servers_combines_and_sorts_both_command_outputs(monkeypatch: Any) -> None:
    outputs = {
        "resolvectl": FakeProc(0, "Global:\nLink 2 (eth0): 192.168.1.1 2001:db8::2\n"),
        "nmcli": FakeProc(0, "IP4.DNS[1]:192.168.1.1\nIP6.DNS[1]:2001:db8::1\n"),
    }
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> FakeProc:
        calls.append(command)
        return outputs[command[0]]

    monkeypatch.setattr(task_module, "run_command", fake_run)
    result = task_module.discover_dns_servers(make_config().dnsproxy_setup, 3.0)
    assert result.ipv4 == ("192.168.1.1",)
    assert result.ipv6 == ("2001:db8::1", "2001:db8::2")
    assert [command[0] for command in calls] == ["resolvectl", "nmcli"]


def test_discover_dns_servers_keeps_one_source_when_other_fails(monkeypatch: Any) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> FakeProc:
        if command[0] == "resolvectl":
            return FakeProc(1, "")
        return FakeProc(0, "IP4.DNS[1]:192.168.1.1\n")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    result = task_module.discover_dns_servers(make_config().dnsproxy_setup, 3.0)
    assert result.ipv4 == ("192.168.1.1",)
    assert result.ipv6 == ()
    assert result.errors == ("resolvectl exited with 1",)


def test_command_contains_all_primary_upstreams_cache_fallback_and_logging() -> None:
    config = make_config()
    command = task_module._command(config.dnsproxy_setup, "39284e")
    assert "--listen=0.0.0.0" in command
    assert "--listen=::" in command
    assert "--upstream=https://dns.nextdns.io/39284e" in command
    assert "--upstream=tls://39284e.dns.nextdns.io" in command
    assert "--upstream=quic://39284e.dns.nextdns.io" in command
    assert "--cache" in command
    for form in (
        "--fallback=1.1.1.1",
        "--fallback=tls://1.1.1.1:853",
        "--fallback=https://1.1.1.1:443/dns-query",
        "--fallback=quic://1.1.1.1:853",
        "--fallback=[2606:4700:4700::1111]",
        "--fallback=tls://[2606:4700:4700::1111]:853",
    ):
        assert form in command
    assert "--output=/var/log/pyntara/dnsproxy.log" in command
    assert "--upstream-mode=load_balance" in command


def test_command_builds_bootstrap_protocol_forms_after_all_other_args() -> None:
    config = make_config()
    command = task_module._command(config.dnsproxy_setup, "39284e")
    for form in (
        "--bootstrap=1.1.1.1",
        "--bootstrap=tls://1.1.1.1:853",
        "--bootstrap=https://1.1.1.1:443/dns-query",
        "--bootstrap=quic://1.1.1.1:853",
        "--bootstrap=[2606:4700:4700::1111]",
        "--bootstrap=tls://[2606:4700:4700::1111]:853",
        "--bootstrap=https://[2606:4700:4700::1111]:443/dns-query",
        "--bootstrap=quic://[2606:4700:4700::1111]:853",
    ):
        assert form in command
    bootstrap_indices = [
        index for index, arg in enumerate(command) if arg.startswith("--bootstrap=")
    ]
    last_other = max(
        index
        for index, arg in enumerate(command)
        if not arg.startswith("--bootstrap=")
    )
    assert bootstrap_indices
    assert min(bootstrap_indices) > last_other


def test_task_writes_root_service_and_resolver_configuration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    install_vault(tmp_path, monkeypatch)
    service_path = tmp_path / "dnsproxy.service"
    config = make_config(
        task_data_root=tmp_path,
        dnsproxy_service_unit_path=service_path,
        dnsproxy_binary_path=tmp_path / "dnsproxy",
        dnsproxy_query_log_path=tmp_path / "dnsproxy.log",
        dnsproxy_profile_id_file_path=tmp_path / "nextdns_profile_id",
        dnsproxy_resolved_conf_dir=tmp_path / "resolved.conf.d",
    )
    context = make_context(vault_password=PASSWORD, config=config)
    monkeypatch.setattr(
        task_module,
        "_release_json",
        lambda *_: {
            "tag_name": "v0.84.1",
            "assets": [
                {
                    "name": "dnsproxy-linux-amd64-v0.84.1.tar.gz",
                    "browser_download_url": "https://example.invalid/dnsproxy.tar.gz",
                }
            ],
        },
    )
    monkeypatch.setattr(task_module, "dpkg_architecture", lambda *_: "amd64")
    monkeypatch.setattr(task_module, "_installed_version", lambda *_: "0.84.1")
    monkeypatch.setattr(task_module, "REPO_ROOT", Path.cwd())
    monkeypatch.setattr(
        task_module, "_write_resolver_dropin", task_module._write_resolver_dropin
    )
    monkeypatch.setattr(task_module, "service_is_enabled", lambda *_: False)
    monkeypatch.setattr(task_module, "service_is_active", lambda *_: False)
    monkeypatch.setattr(task_module, "_wait_active", lambda *_: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        task_module,
        "run_command",
        lambda command, **kwargs: calls.append(command) or FakeProc(0, ""),
    )
    result = task_module.task(context)
    assert result.success is True
    assert service_path.read_text(encoding="utf-8").startswith("[Unit]")
    assert all(directive in (
        config.dnsproxy_setup.resolved_conf_dir
        / config.dnsproxy_setup.resolved_dropin_file_name
    ).read_text(encoding="utf-8") for directive in config.dnsproxy_setup.resolved_dns_directives)
    assert any(command[:2] == ["systemctl", "enable"] for command in calls)


def test_release_asset_selection_rejects_unsupported_architecture() -> None:
    try:
        task_module._asset_for_architecture(
            {"tag_name": "v0.84.1", "assets": []}, "riscv64"
        )
    except RuntimeError as error:
        assert "unsupported" in str(error)
    else:
        raise AssertionError("unsupported architecture was accepted")
