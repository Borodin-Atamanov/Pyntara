from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from support import FakeProc, make_config, make_context

from pyntara.tasks import dnsproxy_setup as task_module

PASSWORD = "password"

ROUTED_STATUS = (
    "Global\n"
    "  resolv.conf mode: stub\n"
    "Current DNS Server: 127.0.0.1:53053\n"
    "       DNS Servers: 127.0.0.1:53053 [::1]:53053\n"
    "        DNS Domain: ~.\n"
    "\n"
    "Link 2 (eth0)\n"
    "Current DNS Server: 195.179.224.53\n"
)


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
    command = task_module._command(
        config.dnsproxy_setup, "39284e", task_module.DiscoveredDnsServers((), (), ())
    )
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
    assert "--cache-size=16777216" in command
    assert "--verbose" not in command


def test_command_builds_bootstrap_protocol_forms_after_all_other_args() -> None:
    config = make_config()
    command = task_module._command(
        config.dnsproxy_setup, "39284e", task_module.DiscoveredDnsServers((), (), ())
    )
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


def test_command_appends_provider_dns_plain_forms_to_fallback_end() -> None:
    config = make_config()
    discovered = task_module.DiscoveredDnsServers(
        ("192.168.1.1",), ("2001:db8::1",), ()
    )
    command = task_module._command(config.dnsproxy_setup, "39284e", discovered)
    fallback_indices = [
        index for index, arg in enumerate(command) if arg.startswith("--fallback=")
    ]
    provider_forms = [command[index] for index in fallback_indices[-2:]]
    assert provider_forms == ["--fallback=192.168.1.1", "--fallback=[2001:db8::1]"]
    assert not any(arg.startswith("--fallback=tls://192.168.1.1") for arg in command)
    assert not any(arg.startswith("--fallback=https://192.168.1.1") for arg in command)
    assert not any(arg.startswith("--fallback=quic://192.168.1.1") for arg in command)


def test_command_appends_provider_dns_plain_forms_to_bootstrap_end() -> None:
    config = make_config()
    discovered = task_module.DiscoveredDnsServers(
        ("192.168.1.1",), ("2001:db8::1",), ()
    )
    command = task_module._command(config.dnsproxy_setup, "39284e", discovered)
    bootstrap_indices = [
        index for index, arg in enumerate(command) if arg.startswith("--bootstrap=")
    ]
    provider_forms = [command[index] for index in bootstrap_indices[-2:]]
    assert provider_forms == ["--bootstrap=192.168.1.1", "--bootstrap=[2001:db8::1]"]
    assert not any(arg.startswith("--bootstrap=tls://192.168.1.1") for arg in command)


def test_command_with_empty_discovery_keeps_configured_pool_only() -> None:
    config = make_config()
    command = task_module._command(
        config.dnsproxy_setup,
        "39284e",
        task_module.DiscoveredDnsServers((), (), ()),
    )
    assert sum(arg.startswith("--fallback=") for arg in command) == 8
    assert sum(arg.startswith("--bootstrap=") for arg in command) == 8


def _run_task(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    append_provider_dns: bool = True,
    provider_dns: bool = False,
    probe: bool = True,
    occupied_port: bool = False,
    stubborn_port: bool = False,
    routing_leftover: bool = False,
    nmcli_active: str = "",
    nmcli_missing: bool = False,
    resolvectl_status_output: str = "",
) -> tuple[Any, Path, list[list[str]], Any]:
    '''Run the dnsproxy task with a uniform command mock and return the
    result, the rendered service path, the recorded commands and the
    built config. provider_dns makes the discovery commands return a
    provider IPv4 and IPv6 resolver pair. occupied_port makes the port
    scan report one listener on the first pass, stubborn_port keeps the
    listener after the kill, routing_leftover keeps the provider address
    in the post-cutover per-link state, and nmcli_active supplies the
    active connection listing. nmcli_missing makes the nmcli check
    command raise FileNotFoundError, and resolvectl_status_output
    overrides the resolvectl status listing; an empty value uses a
    listing that routes through dnsproxy. probe controls the pre-cutover
    dnsproxy answer check.'''
    service_path = tmp_path / "dnsproxy.service"
    config = make_config(
        task_data_root=tmp_path,
        dnsproxy_service_unit_path=service_path,
        dnsproxy_binary_path=tmp_path / "dnsproxy",
        dnsproxy_query_log_path=tmp_path / "dnsproxy.log",
        dnsproxy_profile_id_file_path=tmp_path / "nextdns_profile_id",
        dnsproxy_resolved_conf_dir=tmp_path / "resolved.conf.d",
    )
    config = replace(
        config,
        dnsproxy_setup=replace(
            config.dnsproxy_setup, append_provider_dns=append_provider_dns
        ),
    )
    (tmp_path / "nextdns_profile_id").write_text("39284e\n", encoding="utf-8")
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
    monkeypatch.setattr(task_module, "_dns_probe_answers", lambda *_: probe)
    calls: list[list[str]] = []
    state = {"ss": 0, "routedns": 0}
    active_list_command = list(config.dnsproxy_setup.nmcli_active_list_command)

    def fake_run(command: list[str], **kwargs: Any) -> FakeProc:
        command = list(command)
        calls.append(command)
        if nmcli_missing and command[0] == "nmcli":
            raise FileNotFoundError("nmcli not found")
        if command[:2] in (["ss", "-lntp"], ["ss", "-lunp"]):
            if occupied_port or stubborn_port:
                state["ss"] += 1
                if stubborn_port or state["ss"] <= 2:
                    return FakeProc(
                        0,
                        "LISTEN 0 4096 *:53053 *:* users:((\"dnsproxy\",pid=12345,fd=9))\n",
                    )
            return FakeProc(0, "")
        if command == active_list_command:
            return FakeProc(0, nmcli_active)
        if command == list(config.dnsproxy_setup.resolvectl_status_command):
            return FakeProc(0, resolvectl_status_output or ROUTED_STATUS)
        if command[:4] == ["nmcli", "-t", "-f", "ipv4.ignore-auto-dns,ipv6.ignore-auto-dns"]:
            return FakeProc(0, "ipv4.ignore-auto-dns:no\nipv6.ignore-auto-dns:no\n")
        if provider_dns and command == ["resolvectl", "dns"]:
            state["routedns"] += 1
            if routing_leftover or state["routedns"] == 1:
                return FakeProc(0, "Global:\nLink 2 (eth0): 192.168.1.1 2001:db8::2\n")
            return FakeProc(0, "Global:\nLink 2 (eth0):\n")
        if provider_dns and command[0] == "nmcli":
            return FakeProc(0, "IP4.DNS[1]:192.168.1.1\nIP6.DNS[1]:2001:db8::1\n")
        return FakeProc(0, "")

    monkeypatch.setattr(task_module, "run_command", fake_run)
    result = task_module.task(context)
    return result, service_path, calls, config


def test_task_writes_root_service_and_resolver_configuration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, service_path, calls, config = _run_task(tmp_path, monkeypatch)
    assert result.success is True
    assert service_path.read_text(encoding="utf-8").startswith("[Unit]")
    assert all(directive in (
        config.dnsproxy_setup.resolved_conf_dir
        / config.dnsproxy_setup.resolved_dropin_file_name
    ).read_text(encoding="utf-8") for directive in config.dnsproxy_setup.resolved_dns_directives)
    assert any(command[:2] == ["systemctl", "enable"] for command in calls)


def test_task_appends_discovered_provider_dns_to_service_unit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, service_path, _, _ = _run_task(tmp_path, monkeypatch, provider_dns=True)
    assert result.success is True
    unit = service_path.read_text(encoding="utf-8")
    assert "--fallback=192.168.1.1" in unit
    assert "--bootstrap=192.168.1.1" in unit
    assert "--fallback=[2001:db8::1]" in unit
    assert "--bootstrap=[2001:db8::1]" in unit
    assert "--fallback=tls://192.168.1.1:853" not in unit


def test_task_skips_provider_dns_discovery_when_disabled(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, service_path, calls, _ = _run_task(
        tmp_path, monkeypatch, append_provider_dns=False
    )
    assert result.success is True
    assert not any(command == ["resolvectl", "dns"] for command in calls)
    unit = service_path.read_text(encoding="utf-8")
    assert "--fallback=192.168.1.1" not in unit
    assert "--bootstrap=192.168.1.1" not in unit


def test_task_fails_early_without_the_nextdns_profile_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
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
    result = task_module.task(context)
    assert result.success is False
    assert "nextdns_setup_system_wide" in (result.error or "")
    assert not service_path.exists()


def test_task_kills_the_process_listening_on_the_port(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, _, calls, _ = _run_task(tmp_path, monkeypatch, occupied_port=True)
    assert result.success is True
    assert ["kill", "12345"] in calls
    assert any(command[:2] == ["systemctl", "start"] for command in calls)


def test_task_fails_when_the_port_stays_occupied(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, _, calls, config = _run_task(tmp_path, monkeypatch, stubborn_port=True)
    assert result.success is False
    assert "still occupied" in (result.error or "")
    assert ["kill", "12345"] in calls
    assert not any(command[:2] == ["systemctl", "start"] for command in calls)
    dropin = (
        config.dnsproxy_setup.resolved_conf_dir
        / config.dnsproxy_setup.resolved_dropin_file_name
    )
    assert not dropin.exists()


def test_task_does_not_change_the_resolver_when_the_probe_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, _, calls, config = _run_task(tmp_path, monkeypatch, probe=False)
    assert result.success is False
    assert "does not answer direct DNS" in (result.error or "")
    assert ["systemctl", "stop", "dnsproxy.service"] in calls
    dropin = (
        config.dnsproxy_setup.resolved_conf_dir
        / config.dnsproxy_setup.resolved_dropin_file_name
    )
    assert not dropin.exists()


def test_task_disables_auto_dns_on_active_connections_by_uuid(
    tmp_path: Path, monkeypatch: Any
) -> None:
    active = (
        "Ataman6a:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa:wlp0\n"
        "lo:bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb:lo\n"
        "ygg:cccccccc-cccc-cccc-cccc-cccccccccccc:tun\n"
    )
    result, _, calls, _ = _run_task(tmp_path, monkeypatch, nmcli_active=active)
    assert result.success is True
    modifies = [
        command
        for command in calls
        if command[:3] == ["nmcli", "connection", "modify"]
    ]
    modified_uuids = {command[3] for command in modifies}
    assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in modified_uuids
    assert "cccccccc-cccc-cccc-cccc-cccccccccccc" in modified_uuids
    assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" not in modified_uuids
    assert all("true" in command for command in modifies)
    reapplies = [
        command
        for command in calls
        if command[:3] == ["nmcli", "device", "reapply"]
    ]
    assert ["nmcli", "device", "reapply", "wlp0"] in reapplies
    assert ["nmcli", "device", "reapply", "tun"] in reapplies
    assert not any(command[-1] == "lo" for command in reapplies)


def test_task_succeeds_when_nmcli_is_missing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, _, calls, _ = _run_task(tmp_path, monkeypatch, nmcli_missing=True)
    assert result.success is True
    nmcli_modifies = [
        command for command in calls
        if command[:3] == ["nmcli", "connection", "modify"]
    ]
    nmcli_reapplies = [
        command for command in calls
        if command[:3] == ["nmcli", "device", "reapply"]
    ]
    assert not nmcli_modifies
    assert not nmcli_reapplies


def test_task_succeeds_with_warning_when_nm_missing_and_resolved_routes_dnsproxy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, _, _, _ = _run_task(
        tmp_path,
        monkeypatch,
        provider_dns=True,
        routing_leftover=True,
        nmcli_missing=True,
    )
    assert result.success is True
    assert any("per-link DNS" in warning for warning in result.warnings)
    assert "routes queries through dnsproxy" in " ".join(result.warnings)


def test_task_fails_when_nm_missing_and_global_dns_does_not_point_at_dnsproxy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    status = (
        "Global\n"
        "  resolv.conf mode: stub\n"
        "Current DNS Server: 195.179.224.53\n"
        "       DNS Servers: 195.179.224.53 209.126.15.53\n"
        "        DNS Domain: ~.\n"
    )
    result, _, _, _ = _run_task(
        tmp_path,
        monkeypatch,
        provider_dns=True,
        routing_leftover=True,
        nmcli_missing=True,
        resolvectl_status_output=status,
    )
    assert result.success is False
    assert "does not point at" in (result.error or "")


def test_task_fails_when_nm_missing_and_resolved_not_in_stub_mode(
    tmp_path: Path, monkeypatch: Any
) -> None:
    status = (
        "Global\n"
        "  resolv.conf mode: foreign\n"
        "Current DNS Server: 127.0.0.1:53053\n"
        "       DNS Servers: 127.0.0.1:53053 [::1]:53053\n"
        "        DNS Domain: ~.\n"
    )
    result, _, _, _ = _run_task(
        tmp_path,
        monkeypatch,
        provider_dns=True,
        routing_leftover=True,
        nmcli_missing=True,
        resolvectl_status_output=status,
    )
    assert result.success is False
    assert "stub resolv.conf mode" in (result.error or "")


def test_task_succeeds_with_warning_when_per_link_provider_dns_survives_but_routing_goes_through_dnsproxy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result, _, calls, config = _run_task(
        tmp_path, monkeypatch, provider_dns=True, routing_leftover=True
    )
    assert result.success is True
    assert any("per-link DNS" in warning for warning in result.warnings)
    assert "routes queries through dnsproxy" in " ".join(result.warnings)
    dropin = (
        config.dnsproxy_setup.resolved_conf_dir
        / config.dnsproxy_setup.resolved_dropin_file_name
    )
    assert dropin.exists()
    assert not any(
        command[:3] == ["systemctl", "stop", "dnsproxy.service"]
        for command in calls
    )


def test_per_link_dns_addresses_matches_whole_tokens_only() -> None:
    output = (
        "Global: 127.0.0.1:53053 [::1]:53053\n"
        "Link 2 (eth0): 2800:810:100:1:200:115:192:29 2800:810:100::15 "
        "2800:810:100::9\n"
        "Link 3 (wlx1):\n"
    )
    addresses = task_module._per_link_dns_addresses(output)
    assert "2800:810:100:1:200:115:192:29" in addresses
    assert "2800:810:100::15" in addresses
    assert "2800:810:100::9" in addresses
    assert "810:100::15" not in addresses


def test_task_fails_when_global_dns_missing_wildcard_routing_domain(
    tmp_path: Path, monkeypatch: Any
) -> None:
    status = (
        "Global\n"
        "  resolv.conf mode: stub\n"
        "Current DNS Server: 127.0.0.1:53053\n"
        "       DNS Servers: 127.0.0.1:53053 [::1]:53053\n"
        "        DNS Domain: local\n"
    )
    result, _, _, _ = _run_task(
        tmp_path, monkeypatch, resolvectl_status_output=status
    )
    assert result.success is False
    assert "no ~. routing domain" in (result.error or "")


def test_release_asset_selection_rejects_unsupported_architecture() -> None:
    try:
        task_module._asset_for_architecture(
            {"tag_name": "v0.84.1", "assets": []}, "riscv64"
        )
    except RuntimeError as error:
        assert "unsupported" in str(error)
    else:
        raise AssertionError("unsupported architecture was accepted")
