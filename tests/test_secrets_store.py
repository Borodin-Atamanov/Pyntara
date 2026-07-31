from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pyntara.secrets_store import VaultSecretsStore


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
    store = VaultSecretsStore(
        default_vault=default_vault,
        production_vault=production_vault,
        use_production=False,
        password_provider=lambda _path: "wrong",
    )

    with pytest.raises(RuntimeError, match="password attempts exhausted"):
        store.load()
