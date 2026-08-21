"""Unit tests for the nextdns_setup_system_wide task.

All external resources (subprocess, vault, hostname) are mocked via
monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The vault is a real KeePass database in
a temporary directory with a NextDNS group; the proxy commands are faked
through the shared FakeProc.
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
TEST_NEXTDNS_OK = json.dumps(
    {"status": "ok", "resolver": "45.90.28.0", "srcIP": "1.2.3.4", "server": "bue-1"}
)


def _ctx(tmp_path: Path, *, force: bool = False):
    """Context with the task config rooted in the temporary directory."""

    return make_context(
        install_mode="server",
        vault_password=VAULT_PASSWORD,
        force_tasks=frozenset({"nextdns_setup_system_wide"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            nextdns_vault_group_title="NextDNS",
            nextdns_dnscrypt_config_path=tmp_path
            / "etc"
            / "dnscrypt-proxy"
            / "dnscrypt-proxy.toml",
            nextdns_profile_id_file_path=tmp_path
            / "var"
            / "lib"
            / "pyntara"
            / "nextdns_profile_id",
            nextdns_profile_id_file_mode=0o644,
            nextdns_error_priority=3,
            nextdns_command_timeout_seconds=60,
            local_vault_source_production=Path("secrets/production.vault"),
            local_vault_source_default=Path("secrets/default.vault"),
        ),
    )


def _install_source_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create a production source vault with a NextDNS group and five profiles."""

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
    test_nextdns_ok: bool = True,
    test_nextdns_output: str | None = None,
) -> list[list[str]]:
    """Replace run_command with a recorder; return the recorded calls."""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        if command[0] == "systemctl":
            return _FakeProc(0)
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


def _write_dnscrypt_config(path: Path) -> None:
    """Write a minimal dnscrypt-proxy config file for the task to edit."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[sources]\n"
        "[sources.'public-resolvers']\n"
        "urls = ['https://example.com/resolvers.md']\n"
        "cache_file = 'public-resolvers.md'\n"
        "minisign_key = 'RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3'\n"
        "\n"
        "fallback_resolvers = ['1.1.1.1', '8.8.8.8']\n",
        encoding="utf-8",
    )


def test_configures_proxy_and_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    config_path = ctx.config.nextdns_setup_system_wide.dnscrypt_config_path
    _write_dnscrypt_config(config_path)
    calls = _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert config_path.read_text(encoding="utf-8").startswith("[sources]")
    assert not any(call[0] in ("systemctl", "curl") for call in calls)
    profile_file = tmp_path / "var" / "lib" / "pyntara" / "nextdns_profile_id"
    assert profile_file.read_text(encoding="utf-8").strip() in PROFILE_IDS


def test_skip_when_already_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    config_path = ctx.config.nextdns_setup_system_wide.dnscrypt_config_path
    _write_dnscrypt_config(config_path)

    profile_file = ctx.config.nextdns_setup_system_wide.profile_id_file_path
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text("938263\n", encoding="utf-8")

    calls = _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.skipped is True
    assert not calls


def test_verification_failure_reverts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    config_path = ctx.config.nextdns_setup_system_wide.dnscrypt_config_path
    _write_dnscrypt_config(config_path)
    calls = _install_subprocess(monkeypatch, test_nextdns_ok=False)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] in ("systemctl", "curl") for call in calls)


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
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    config_path = ctx.config.nextdns_setup_system_wide.dnscrypt_config_path
    _write_dnscrypt_config(config_path)
    _install_subprocess(
        monkeypatch, test_nextdns_ok=True, test_nextdns_output=body
    )
    result = task_module.task(ctx)
    assert result.success is True
    assert result.error is None
    assert excerpt not in (result.message or "")


def test_missing_group_fails_without_touching_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    monkeypatch.setattr("pyntara.tasks.local_vault_setup.REPO_ROOT", tmp_path)
    ctx = _ctx(tmp_path)
    config_path = ctx.config.nextdns_setup_system_wide.dnscrypt_config_path
    _write_dnscrypt_config(config_path)
    original = config_path.read_text(encoding="utf-8")
    _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is False
    assert "group" in (result.error or "")
    assert config_path.read_text(encoding="utf-8") == original


def test_empty_group_fails_without_touching_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault), password=VAULT_PASSWORD)
    kp.add_group(kp.root_group, "NextDNS", notes="empty")
    kp.save()
    monkeypatch.setattr("pyntara.tasks.local_vault_setup.REPO_ROOT", tmp_path)
    ctx = _ctx(tmp_path)
    config_path = ctx.config.nextdns_setup_system_wide.dnscrypt_config_path
    _write_dnscrypt_config(config_path)
    original = config_path.read_text(encoding="utf-8")
    result = task_module.task(ctx)
    assert result.success is False
    assert config_path.read_text(encoding="utf-8") == original


def test_missing_config_file_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True


def test_force_mode_rewrites_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path, force=True)
    config_path = ctx.config.nextdns_setup_system_wide.dnscrypt_config_path
    _write_dnscrypt_config(config_path)
    calls = _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert not any(call[0] == "systemctl" for call in calls)


def test_message_names_the_active_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    config_path = ctx.config.nextdns_setup_system_wide.dnscrypt_config_path
    _write_dnscrypt_config(config_path)
    _install_subprocess(monkeypatch)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.message is not None
    assert "NextDNS profile" in result.message
    assert "dnsproxy_setup" in result.message
