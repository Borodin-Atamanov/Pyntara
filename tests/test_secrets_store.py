from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pyntara.secrets_store import VaultSecretsStore, _group_path

# ---------------------------------------------------------------------------
# Existing tests (kept as-is)
# ---------------------------------------------------------------------------


def test_load_reads_yaml_vault_mapping(tmp_path: Path) -> None:
    default_vault = tmp_path / "default.vault"
    production_vault = tmp_path / "production.vault"
    default_vault.write_text("alpha: one\nbeta: two\n", encoding="utf-8")
    production_vault.write_text("alpha: production\n", encoding="utf-8")

    store = VaultSecretsStore(
        default_vault=default_vault,
        production_vault=production_vault,
        use_production=False,
    )
    store.load()

    assert store.get("alpha") == "one"
    assert store.get("beta") == "two"


def test_load_reads_keepass_vault_without_utf8_decode_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_vault = tmp_path / "default.vault"
    production_vault = tmp_path / "production.vault"
    default_vault.write_bytes(b"\x03\xd9\xa2\x9a\x00\x00\x00\x00")
    production_vault.write_text("unused: true\n", encoding="utf-8")

    @dataclass(frozen=True)
    class FakeGroup:
        name: str | None
        parentgroup: object | None

    @dataclass(frozen=True)
    class FakeEntry:
        title: str | None
        password: str | None
        group: object | None

    class FakeDatabase:
        entries = [
            FakeEntry(
                title="salt",
                password="abc123",
                group=FakeGroup(name="core", parentgroup=FakeGroup(name="Root", parentgroup=None)),
            )
        ]

    class FakePyKeePassModule:
        class CredentialsError(Exception):
            pass

        @staticmethod
        def open(vault_path: Path, *, password: str) -> FakeDatabase:
            assert vault_path == default_vault
            assert password == "pw"
            return FakeDatabase()

    monkeypatch.setattr("pyntara.secrets_store._import_pykeepass", lambda: FakePyKeePassModule())
    monkeypatch.setattr("pyntara.secrets_store._interactive_prompt_available", lambda: True)
    store = VaultSecretsStore(
        default_vault=default_vault,
        production_vault=production_vault,
        use_production=False,
        password_provider=lambda _path: "pw",
    )

    store.load()

    assert store.get("core.salt") == "abc123"


def test_load_raises_after_keepass_password_attempts_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_vault = tmp_path / "default.vault"
    production_vault = tmp_path / "production.vault"
    default_vault.write_bytes(b"\x03\xd9\xa2\x9a\x00\x00\x00\x00")
    production_vault.write_text("unused: true\n", encoding="utf-8")

    class FakePyKeePassModule:
        class CredentialsError(Exception):
            pass

        @staticmethod
        def open(vault_path: Path, *, password: str) -> object:
            del vault_path, password
            raise FakePyKeePassModule.CredentialsError("wrong")

    monkeypatch.setattr("pyntara.secrets_store._import_pykeepass", lambda: FakePyKeePassModule())
    monkeypatch.setattr("pyntara.secrets_store._interactive_prompt_available", lambda: True)
    store = VaultSecretsStore(
        default_vault=default_vault,
        production_vault=production_vault,
        use_production=False,
        password_provider=lambda _path: "wrong",
    )

    with pytest.raises(RuntimeError, match="password attempts exhausted"):
        store.load()


def test_load_fails_fast_when_noninteractive_keepass_has_no_env_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without env var, password file, or custom provider, loading fails."""
    default_vault = tmp_path / "default.vault"
    production_vault = tmp_path / "production.vault"
    default_vault.write_bytes(b"\x03\xd9\xa2\x9a\x00\x00\x00\x00")
    production_vault.write_text("unused: true\n", encoding="utf-8")

    monkeypatch.delenv("PYNTARA_VAULT_PASSWORD", raising=False)
    store = VaultSecretsStore(
        default_vault=default_vault,
        production_vault=production_vault,
        use_production=False,
    )

    with pytest.raises(RuntimeError, match="Failed to open default KeePass vault"):
        store.load()


# ---------------------------------------------------------------------------
# New tests
# ---------------------------------------------------------------------------


# --- 1. Integration test: real KeePass database via pykeepass ---

pykeepass = pytest.importorskip("pykeepass", reason="pykeepass is required for integration test")


def test_load_reads_real_keepass_database(tmp_path: Path) -> None:
    """Create a real KDBX file, populate it with the expected schema,
    then load it through VaultSecretsStore and verify all keys."""
    vault_path = tmp_path / "test.vault"
    password = "test-password-123"

    # Create a new KeePass database
    pykeepass.create_database(str(vault_path), password=password)
    kp = pykeepass.PyKeePass(str(vault_path), password=password)

    # Build the group hierarchy and entries matching SCHEMA from generate_new_keepass_db.py
    # Group: meta
    meta_group = kp.add_group(kp.root_group, "meta")
    kp.add_entry(meta_group, title="schema_version", username="pyntara", password="1")

    # Group: core
    core_group = kp.add_group(kp.root_group, "core")
    kp.add_entry(core_group, title="salt", username="pyntara", password="test-salt-value")

    # Group: telemetry > telegram
    telemetry_group = kp.add_group(kp.root_group, "telemetry")
    telegram_group = kp.add_group(telemetry_group, "telegram")
    kp.add_entry(
        telegram_group,
        title="bot_token",
        username="telegram_bot",
        password="test-bot-token",
    )
    kp.add_entry(telegram_group, title="chat_id", username="telegram_chat", password="test-chat-id")

    # Group: telemetry > gdrive
    gdrive_group = kp.add_group(telemetry_group, "gdrive")
    kp.add_entry(
        gdrive_group,
        title="service_account_json",
        username="google_drive",
        password='{"type": "service_account"}',
    )

    # Group: network > proxy_remote
    network_group = kp.add_group(kp.root_group, "network")
    proxy_group = kp.add_group(network_group, "proxy_remote")
    kp.add_entry(proxy_group, title="host", username="proxy", password="proxy.example.com")
    kp.add_entry(proxy_group, title="port", username="proxy", password="8080")
    kp.add_entry(proxy_group, title="username", username="proxy", password="proxy-user")
    kp.add_entry(proxy_group, title="password", username="proxy", password="proxy-pass")

    # Group: network > nextdns
    nextdns_group = kp.add_group(network_group, "nextdns")
    kp.add_entry(
        nextdns_group,
        title="profile_dns",
        username="nextdns",
        password="abc123.dns.nextdns.io",
    )

    kp.save()
    # PyKeePass instance is closed after save; we reload via VaultSecretsStore

    store = VaultSecretsStore(
        default_vault=vault_path,
        production_vault=tmp_path / "production.vault",
        use_production=False,
        password_provider=lambda _: password,
    )
    store.load()

    # Verify all expected keys
    assert store.get("meta.schema_version") == "1"
    assert store.get("core.salt") == "test-salt-value"
    assert store.get("telemetry.telegram.bot_token") == "test-bot-token"
    assert store.get("telemetry.telegram.chat_id") == "test-chat-id"
    assert store.get("telemetry.gdrive.service_account_json") == '{"type": "service_account"}'
    assert store.get("network.proxy_remote.host") == "proxy.example.com"
    assert store.get("network.proxy_remote.port") == "8080"
    assert store.get("network.proxy_remote.username") == "proxy-user"
    assert store.get("network.proxy_remote.password") == "proxy-pass"
    assert store.get("network.nextdns.profile_dns") == "abc123.dns.nextdns.io"

    # Verify missing key returns None and default
    assert store.get("nonexistent.key") is None
    assert store.get("nonexistent.key", "default") == "default"


# --- 2-4. Unit tests for _group_path ---


def test_group_path_skips_root_group() -> None:
    """Root group must be excluded from the path."""

    @dataclass(frozen=True)
    class Group:
        name: str | None
        parentgroup: object | None

    root = Group(name="Root", parentgroup=None)
    child = Group(name="telemetry", parentgroup=root)
    leaf = Group(name="telegram", parentgroup=child)

    assert _group_path(leaf) == ["telemetry", "telegram"]


def test_group_path_returns_empty_for_root() -> None:
    """Root group alone must produce an empty path."""

    @dataclass(frozen=True)
    class Group:
        name: str | None
        parentgroup: object | None

    root = Group(name="Root", parentgroup=None)
    assert _group_path(root) == []


def test_group_path_handles_none_name() -> None:
    """A group with name=None must not break the path."""

    @dataclass(frozen=True)
    class Group:
        name: str | None
        parentgroup: object | None

    root = Group(name="Root", parentgroup=None)
    unnamed = Group(name=None, parentgroup=root)

    assert _group_path(unnamed) == []


# --- 5. Guard clause: get() before load() ---


def test_get_raises_before_load(tmp_path: Path) -> None:
    """Calling get() before load() must raise RuntimeError."""
    default_vault = tmp_path / "default.vault"
    production_vault = tmp_path / "production.vault"
    default_vault.write_text("key: value\n", encoding="utf-8")
    production_vault.write_text("unused: true\n", encoding="utf-8")

    store = VaultSecretsStore(
        default_vault=default_vault,
        production_vault=production_vault,
        use_production=False,
    )

    with pytest.raises(RuntimeError, match="Secrets store must be loaded before use"):
        store.get("key")


# --- 6. PYNTARA_VAULT_PASSWORD environment variable ---


def test_load_uses_env_password_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When PYNTARA_VAULT_PASSWORD is set, password_provider must not be called."""
    monkeypatch.setenv("PYNTARA_VAULT_PASSWORD", "env-password")

    default_vault = tmp_path / "default.vault"
    production_vault = tmp_path / "production.vault"
    default_vault.write_bytes(b"\x03\xd9\xa2\x9a\x00\x00\x00\x00")
    production_vault.write_text("unused: true\n", encoding="utf-8")

    provider_called = False

    def failing_provider(_path: Path) -> str:
        nonlocal provider_called
        provider_called = True
        msg = "password_provider should not be called when PYNTARA_VAULT_PASSWORD is set"
        raise AssertionError(msg)

    @dataclass(frozen=True)
    class FakeGroup:
        name: str | None
        parentgroup: object | None

    @dataclass(frozen=True)
    class FakeEntry:
        title: str | None
        password: str | None
        group: object | None

    class FakeDatabase:
        entries = [
            FakeEntry(
                title="salt",
                password="from-env",
                group=FakeGroup(name="core", parentgroup=FakeGroup(name="Root", parentgroup=None)),
            )
        ]

    class FakePyKeePassModule:
        class CredentialsError(Exception):
            pass

        @staticmethod
        def open(vault_path: Path, *, password: str) -> FakeDatabase:
            assert password == "env-password", "Must use password from env"
            return FakeDatabase()

    monkeypatch.setattr("pyntara.secrets_store._import_pykeepass", lambda: FakePyKeePassModule())
    monkeypatch.setattr("pyntara.secrets_store._interactive_prompt_available", lambda: True)

    store = VaultSecretsStore(
        default_vault=default_vault,
        production_vault=production_vault,
        use_production=False,
        password_provider=failing_provider,
    )
    store.load()

    assert store.get("core.salt") == "from-env"
    assert not provider_called, "password_provider must not be called"


# --- 7. use_production=True flag ---


def test_load_uses_production_vault_when_flag_set(tmp_path: Path) -> None:
    """With use_production=True, production.vault must be loaded instead of default.vault."""
    default_vault = tmp_path / "default.vault"
    production_vault = tmp_path / "production.vault"
    default_vault.write_text("env: default\n", encoding="utf-8")
    production_vault.write_text("env: production\n", encoding="utf-8")

    store = VaultSecretsStore(
        default_vault=default_vault,
        production_vault=production_vault,
        use_production=True,
    )
    store.load()

    assert store.get("env") == "production"
