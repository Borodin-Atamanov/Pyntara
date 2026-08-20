"""Unit tests for the nextdns_setup_system_wide task.

All external resources (subprocess, vault, hostname) are mocked via
monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The vault is a real KeePass database in
a temporary directory with a NextDNS group; the resolver commands are
faked through the shared FakeProc.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pykeepass import PyKeePass, create_database
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.tasks import nextdns_setup_system_wide as task_module

VAULT_PASSWORD = "local-vault-password"
PROFILE_IDS = ("39284e", "938263", "a47276", "b2e82c", "cb3874")
RESOLVE_STATUS_OK = """Global
    Protocols: -LLMNR -mDNS -DNSOverTLS DNSSEC=no/unsupported
  resolv.conf mode: stub

Link 2 (enp87s0)
    Current Scopes: DNS
         Protocols: +DefaultRoute -LLMNR -mDNS -DNSOverTLS DNSSEC=no/unsupported
     Current DNS Server: 45.90.28.0
       DNS Servers: 45.90.28.0 45.90.30.0 2a07:a8c0::6c:7f39
"""

TEST_NEXTDNS_OK = json.dumps(
    {"status": "ok", "resolver": "45.90.28.0", "srcIP": "1.2.3.4", "server": "bue-1"}
)


def _ctx(tmp_path: Path, *, force: bool = False, manage_nm: bool = True):
    """Context with the task config rooted in the temporary directory.

    vault_password carries the run password that opens the source vault;
    the source vault paths live under the temporary repository root, so
    the task reads the profiles through the same source resolution as
    local_vault_setup.
    """

    return make_context(
        install_mode="server",
        vault_password=VAULT_PASSWORD,
        force_tasks=frozenset({"nextdns_setup_system_wide"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            nextdns_vault_group_title="NextDNS",
            nextdns_resolved_conf_dir=tmp_path / "etc" / "systemd" / "resolved.conf.d",
            nextdns_dropin_file_name="pyntara.conf",
            nextdns_dropin_file_mode=0o644,
            nextdns_dns_over_tls="opportunistic",
            nextdns_fallback_dns=("1.1.1.1", "9.9.9.9"),
            nextdns_manage_networkmanager=manage_nm,
            nextdns_error_priority=3,
            nextdns_command_timeout_seconds=60,
            local_vault_source_production=Path("secrets/production.vault"),
            local_vault_source_default=Path("secrets/default.vault"),
        ),
    )


def _install_source_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create a production source vault with a NextDNS group and five profiles.

    The vault lives at the temporary repository root under secrets/, the
    same relative path the config names; the task resolves it through
    REPO_ROOT, so the monkeypatched module REPO_ROOT must point at the
    temporary root before the task runs.
    """

    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault), password=VAULT_PASSWORD)
    group = kp.add_group(kp.root_group, "NextDNS", notes="test profiles")
    for profile_id in PROFILE_IDS:
        kp.add_entry(group, f"{profile_id} profile", profile_id, "")
    kp.save()
    monkeypatch.setattr("pyntara.tasks.local_vault_setup.REPO_ROOT", tmp_path)


def _install_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    nm_present: bool = True,
    resolvectl_ok: bool = True,
    test_nextdns_ok: bool = True,
    test_nextdns_output: str | None = None,
) -> list[list[str]]:
    """Replace run_command with a recorder; return the recorded calls."""

    calls: list[list[str]] = []
    nm_connections = ["Wired", "Wifi"]

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "nmcli":
            if command[1] == "--version":
                return _FakeProc(0 if nm_present else 127, "")
            if command[1] == "-t":
                return _FakeProc(0, "\n".join(nm_connections) + "\n")
            return _FakeProc(0)
        if command[0] == "systemctl":
            return _FakeProc(0)
        if command[0] == "resolvectl":
            if resolvectl_ok:
                return _FakeProc(0, RESOLVE_STATUS_OK)
            return _FakeProc(1, "")
        if command[0] == "curl":
            if test_nextdns_ok:
                output = (
                    TEST_NEXTDNS_OK
                    if test_nextdns_output is None
                    else test_nextdns_output
                )
                return _FakeProc(0, output)
            return _FakeProc(7, "")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.tasks.nextdns_setup_system_wide.run_command", fake_run)
    return calls


def test_configures_resolver_and_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The task writes the drop-in, restarts the resolver, disables per-link
    # DNS and verifies through test.nextdns.io.
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    calls = _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    dropin = tmp_path / "etc" / "systemd" / "resolved.conf.d" / "pyntara.conf"
    content = dropin.read_text(encoding="utf-8")
    assert "[Resolve]" in content
    assert "DNS=" in content
    assert ".dns.nextdns.io" in content
    assert "FallbackDNS=1.1.1.1 9.9.9.9" in content
    assert "DNSOverTLS=opportunistic" in content
    assert any(
        call[0] == "nmcli" and any("ignore-auto-dns" in arg for arg in call)
        for call in calls
    )
    assert any(call[0] == "curl" for call in calls)


def test_skip_when_already_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An already-matching drop-in plus a working verification means the
    # task skips without changing anything.
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    dropin = tmp_path / "etc" / "systemd" / "resolved.conf.d" / "pyntara.conf"
    dropin.parent.mkdir(parents=True)
    import socket

    from pyntara.nextdns import resolve_servers, select_profile_id

    cfg = ctx.config.nextdns_setup_system_wide
    profile_id = select_profile_id(socket.gethostname(), PROFILE_IDS)
    lines = [
        cfg.dropin_header,
        cfg.resolve_section,
        (
            "DNS="
            + " ".join(
                resolve_servers(
                    profile_id,
                    cfg.ipv4_servers,
                    cfg.ipv6_prefixes,
                    cfg.dot_endpoint_format,
                )
            )
        ),
        "FallbackDNS=1.1.1.1 9.9.9.9",
        "DNSOverTLS=opportunistic",
        f"Domains={cfg.domains_directive}",
    ]
    dropin.write_text("\n".join(lines) + "\n", encoding="utf-8")
    calls = _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.skipped is True
    assert not any("systemctl" in call for call in calls)


def test_verification_failure_reverts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When test.nextdns.io does not answer, the drop-in is removed and the
    # NetworkManager flags are restored, so the machine keeps its previous
    # resolver configuration.
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    calls = _install_subprocess(monkeypatch, test_nextdns_ok=False)
    result = task_module.task(ctx)
    assert result.success is False
    dropin = tmp_path / "etc" / "systemd" / "resolved.conf.d" / "pyntara.conf"
    assert not dropin.exists()
    # The revert sets ignore-auto-dns back to false on every connection.
    assert any(
        call[0] == "nmcli" and call[1] == "connection" and "false" in call
        for call in calls
    )


@pytest.mark.parametrize(
    ("body", "excerpt"),
    [
        ("", "<empty body>"),
        ("<html>blocked</html>", "<html>blocked</html>"),
    ],
)
def test_non_json_verification_body_reports_excerpt_and_reverts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    excerpt: str,
) -> None:
    # A successful curl that returns a non-JSON body (the regression
    # test.nextdns.io exposed: a 302 with an empty body) fails the
    # verification, names the body excerpt and reverts the drop-in.
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    calls = _install_subprocess(
        monkeypatch, test_nextdns_ok=True, test_nextdns_output=body
    )
    result = task_module.task(ctx)
    assert result.success is False
    assert "did not return JSON" in (result.error or "")
    assert excerpt in (result.error or "")
    dropin = tmp_path / "etc" / "systemd" / "resolved.conf.d" / "pyntara.conf"
    assert not dropin.exists()
    assert any(
        call[0] == "nmcli" and call[1] == "connection" and "false" in call
        for call in calls
    )


def test_missing_group_fails_without_touching_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A source vault without the NextDNS group fails the task and never
    # writes the drop-in.
    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    monkeypatch.setattr("pyntara.tasks.local_vault_setup.REPO_ROOT", tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is False
    assert "group" in (result.error or "")
    dropin = tmp_path / "etc" / "systemd" / "resolved.conf.d" / "pyntara.conf"
    assert not dropin.exists()
    assert not any(call[0] == "nmcli" for call in calls)


def test_empty_group_fails_without_touching_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty NextDNS group fails the task instead of picking nothing.
    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault), password=VAULT_PASSWORD)
    kp.add_group(kp.root_group, "NextDNS", notes="empty")
    kp.save()
    monkeypatch.setattr("pyntara.tasks.local_vault_setup.REPO_ROOT", tmp_path)
    ctx = _ctx(tmp_path)
    result = task_module.task(ctx)
    assert result.success is False
    assert "no profiles" in (result.error or "")


def test_vault_unavailable_fails_without_touching_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing source vault and an unavailable runtime vault fail the
    # task before anything is written.
    monkeypatch.setattr("pyntara.tasks.local_vault_setup.REPO_ROOT", tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is False
    assert "cannot open a vault" in (result.error or "")
    dropin = tmp_path / "etc" / "systemd" / "resolved.conf.d" / "pyntara.conf"
    assert not dropin.exists()
    assert not any(call[0] == "nmcli" for call in calls)


def test_dropin_merges_without_touching_other_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pre-existing drop-in with a foreign line keeps the foreign line
    # and gains the missing directives, instead of being overwritten.
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    dropin = tmp_path / "etc" / "systemd" / "resolved.conf.d" / "pyntara.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_text(
        "[Resolve]\nCache=no-negative\nForeignKey=value\n", encoding="utf-8"
    )
    _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    content = dropin.read_text(encoding="utf-8")
    assert "ForeignKey=value" in content
    assert "Cache=no-negative" in content
    assert "DNS=" in content
    assert "DNSOverTLS=opportunistic" in content
    assert "Domains=~." in content


def test_profile_change_replaces_dns_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When the hostname changes and the profile choice moves to another
    # profile, the DNS= directive is replaced, not stacked: a stale
    # profile must never keep serving the machine.
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    dropin = tmp_path / "etc" / "systemd" / "resolved.conf.d" / "pyntara.conf"
    dropin.parent.mkdir(parents=True)

    import socket

    from pyntara.nextdns import resolve_servers, select_profile_id

    cfg = ctx.config.nextdns_setup_system_wide
    monkeypatch.setattr(socket, "gethostname", lambda: "host-0")
    old_profile = select_profile_id("host-0", PROFILE_IDS)
    dropin.write_text(
        f"{cfg.dropin_header}\n"
        f"{cfg.resolve_section}\n"
        "DNS="
        + " ".join(
            resolve_servers(
                old_profile,
                cfg.ipv4_servers,
                cfg.ipv6_prefixes,
                cfg.dot_endpoint_format,
            )
        )
        + "\n"
        "FallbackDNS=1.1.1.1 9.9.9.9\n"
        "DNSOverTLS=opportunistic\n"
        f"Domains={cfg.domains_directive}\n"
        "Cache=no-negative\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(socket, "gethostname", lambda: "host-1")
    new_profile = select_profile_id("host-1", PROFILE_IDS)
    assert new_profile != old_profile

    _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    content = dropin.read_text(encoding="utf-8")
    dns_lines = [
        line for line in content.splitlines() if line.startswith("DNS=")
    ]
    assert len(dns_lines) == 1
    assert new_profile in dns_lines[0]
    assert old_profile not in dns_lines[0]
    assert "Cache=no-negative" in content


def test_message_names_the_active_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The result message names the profile and the fallback count, so the
    # run report shows what the machine actually resolves through.
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.message is not None
    assert "NextDNS profile" in result.message
    assert "fallback" in result.message
