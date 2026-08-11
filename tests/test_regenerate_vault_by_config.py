"""Tests for the standalone vault regeneration script.

The script secrets/regenerate_vault_by_config.py is imported as a module
through importlib.util (the secrets directory is not a package) and its
functions are exercised against real KeePass databases in temporary
directories. The config path, the environment, the terminal state and the
interactive prompt are injected via monkeypatch, so the real config.toml,
the real environment and the real stdin are never touched.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pykeepass import PyKeePass, create_database
from pykeepass.exceptions import CredentialsError

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "secrets" / "regenerate_vault_by_config.py"

DEFAULT_ENTRIES = [
    {"title": "password_salt", "notes": "Primary salt for password derivation."},
    {"title": "pyntara_local_vault_password", "notes": "Password for the runtime secret vault."},
    {
        "title": "google_script_key",
        "notes": "Auth key of the System Metrics Google Drive web app.",
    },
]

VAULT_PASSWORD = "vault-secret"


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    """The script loaded as a module from its file location."""

    spec = importlib.util.spec_from_file_location(
        "regenerate_vault_by_config", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _NonTtyStdin:
    def isatty(self) -> bool:
        return False


class _TtyStdin:
    def isatty(self) -> bool:
        return True


class _FakeGetpass:
    def getpass(self, prompt: str) -> str:
        return "typed-password"


@pytest.fixture(autouse=True)
def _isolate_environment(
    gen: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real environment, terminal and prompt must never leak into tests.
    monkeypatch.delenv("PYNTARA_VAULT_PASSWORD", raising=False)
    monkeypatch.setattr(
        gen, "sys", SimpleNamespace(stdin=_NonTtyStdin(), stderr=sys.stderr)
    )
    monkeypatch.setattr(gen, "getpass", _FakeGetpass())


def _write_config(tmp_path: Path, entries: list[dict[str, str]]) -> Path:
    """A minimal config.toml whose [vault_structure] carries the entries."""

    lines = ["[vault_structure]"]
    for entry in entries:
        lines.append("")
        lines.append("[[vault_structure.entries]]")
        for name, value in entry.items():
            lines.append(f"{name} = {json.dumps(value)}")
    config_path = tmp_path / "config.toml"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def _prepare(
    gen: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entries: list[dict[str, str]] | None = None,
    env_password: str | None = VAULT_PASSWORD,
) -> None:
    """Point the script at a temp config and set the password source."""

    config_path = _write_config(
        tmp_path, entries if entries is not None else DEFAULT_ENTRIES
    )
    monkeypatch.setattr(gen, "CONFIG_PATH", config_path)
    if env_password is not None:
        monkeypatch.setenv("PYNTARA_VAULT_PASSWORD", env_password)


def _opens_with(path: Path, password: str) -> bool:
    try:
        PyKeePass(str(path), password=password)
    except CredentialsError:
        return False
    return True


def test_creates_vault_when_file_absent(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing vault must be created from the config with every entry in
    # the root group, and it must open with the provided password.
    _prepare(gen, tmp_path, monkeypatch)
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    kp = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    entries = kp.find_entries(group=kp.root_group, recursive=False, first=False)
    assert entries is not None
    assert {entry.title for entry in entries} == {
        "password_salt",
        "pyntara_local_vault_password",
        "google_script_key",
    }
    salt = kp.find_entries(
        title="password_salt", group=kp.root_group, recursive=False, first=True
    )
    assert salt is not None
    assert salt.notes == "Primary salt for password derivation."
    assert not salt.password
    script = kp.find_entries(
        title="google_script_key", group=kp.root_group, recursive=False, first=True
    )
    assert script is not None
    assert script.notes == "Auth key of the System Metrics Google Drive web app."
    assert not script.password


def test_creates_vault_when_file_empty(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A zero-byte file is treated as absent and recreated from the config.
    _prepare(gen, tmp_path, monkeypatch)
    vault_path = tmp_path / "default.vault"
    vault_path.write_bytes(b"")
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    assert _opens_with(vault_path, VAULT_PASSWORD)


def test_overwrite_recreates_vault(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --overwrite discards the old database and its password and rebuilds
    # the vault from the config; entries outside the config are lost.
    _prepare(gen, tmp_path, monkeypatch)
    vault_path = tmp_path / "vault.kdbx"
    create_database(str(vault_path), password="old-password")
    kp = PyKeePass(str(vault_path), password="old-password")
    kp.add_entry(kp.root_group, "legacy_entry", "legacy-user", "old-pass")
    kp.save()
    assert gen.main([str(vault_path), "--overwrite"]) == gen.EXIT_OK
    assert _opens_with(vault_path, VAULT_PASSWORD)
    assert not _opens_with(vault_path, "old-password")
    kp2 = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    entries = kp2.find_entries(group=kp2.root_group, recursive=False, first=False)
    assert entries is not None
    assert {entry.title for entry in entries} == {
        "password_salt",
        "pyntara_local_vault_password",
        "google_script_key",
    }


def test_update_adds_missing_entries_and_keeps_existing(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An existing vault gains only the entries missing from the root group;
    # every other entry keeps its values untouched.
    _prepare(gen, tmp_path, monkeypatch)
    vault_path = tmp_path / "vault.kdbx"
    create_database(str(vault_path), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    kp.add_entry(kp.root_group, "password_salt", "legacy-user", "existing-salt")
    kp.add_entry(kp.root_group, "extra_entry", "user", "keep-me")
    kp.save()
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    kp2 = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    salt = kp2.find_entries(
        title="password_salt", group=kp2.root_group, recursive=False, first=True
    )
    assert salt is not None
    assert salt.password == "existing-salt"
    assert salt.username == "legacy-user"
    added = kp2.find_entries(
        title="pyntara_local_vault_password",
        group=kp2.root_group,
        recursive=False,
        first=True,
    )
    assert added is not None
    assert not added.password
    assert added.notes == "Password for the runtime secret vault."
    extra = kp2.find_entries(
        title="extra_entry", group=kp2.root_group, recursive=False, first=True
    )
    assert extra is not None
    assert extra.password == "keep-me"


def test_noop_when_vault_matches(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When every config entry is already present, the file must not change.
    _prepare(gen, tmp_path, monkeypatch)
    vault_path = tmp_path / "vault.kdbx"
    create_database(str(vault_path), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    for entry in DEFAULT_ENTRIES:
        kp.add_entry(
            kp.root_group,
            title=entry["title"],
            username="",
            password="",
            url=None,
            notes=entry["notes"],
        )
    kp.save()
    before = vault_path.read_bytes()
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    assert vault_path.read_bytes() == before


def test_env_password_wins_over_password_file(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The environment variable has priority over the password file.
    _prepare(gen, tmp_path, monkeypatch, env_password="from-env")
    (tmp_path / "default.password").write_text("from-file\n", encoding="utf-8")
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    assert _opens_with(vault_path, "from-env")
    assert not _opens_with(vault_path, "from-file")


def test_password_file_used_and_stripped(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without an environment password the .password file is read and its
    # surrounding whitespace and trailing newline are trimmed.
    _prepare(gen, tmp_path, monkeypatch, env_password=None)
    (tmp_path / "default.password").write_text(
        "  from-file  \n", encoding="utf-8"
    )
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    assert _opens_with(vault_path, "from-file")


def test_empty_env_falls_through_to_password_file(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty environment variable is treated as absent, not as a password.
    _prepare(gen, tmp_path, monkeypatch, env_password="")
    (tmp_path / "default.password").write_text("from-file\n", encoding="utf-8")
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    assert _opens_with(vault_path, "from-file")


def test_prompt_used_when_no_env_or_file(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With a terminal stdin the interactive prompt supplies the password.
    _prepare(gen, tmp_path, monkeypatch, env_password=None)
    monkeypatch.setattr(
        gen, "sys", SimpleNamespace(stdin=_TtyStdin(), stderr=sys.stderr)
    )
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    assert _opens_with(vault_path, "typed-password")


def test_no_password_available_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No environment password, no password file, non-terminal stdin: the
    # script must fail without creating anything.
    _prepare(gen, tmp_path, monkeypatch, env_password=None)
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert not vault_path.exists()


def test_unreadable_password_file_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A password file that exists but cannot be read is a fatal error.
    _prepare(gen, tmp_path, monkeypatch, env_password=None)
    (tmp_path / "default.password").mkdir()
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert not vault_path.exists()


def test_wrong_password_for_existing_vault_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A wrong password must fail without touching the existing vault.
    _prepare(gen, tmp_path, monkeypatch, env_password="wrong")
    vault_path = tmp_path / "vault.kdbx"
    create_database(str(vault_path), password="right")
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert _opens_with(vault_path, "right")


def test_broken_nonempty_file_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-empty file that is not a KeePass database must fail without
    # being overwritten.
    _prepare(gen, tmp_path, monkeypatch)
    vault_path = tmp_path / "vault.kdbx"
    payload = b"this is not a keepass database"
    vault_path.write_bytes(payload)
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert vault_path.read_bytes() == payload


def test_unknown_config_field_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A field name that is not a KeePass entry field is a config error and
    # nothing is created.
    _prepare(
        gen,
        tmp_path,
        monkeypatch,
        entries=[{"title": "a", "notes": "n", "colour": "red"}],
    )
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert not vault_path.exists()


def test_missing_config_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The config is mandatory; without it the script fails cleanly.
    _prepare(gen, tmp_path, monkeypatch)
    monkeypatch.setattr(gen, "CONFIG_PATH", tmp_path / "missing.toml")
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert not vault_path.exists()


def test_duplicate_titles_in_config_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare(
        gen,
        tmp_path,
        monkeypatch,
        entries=[{"title": "a", "notes": "first"}, {"title": "a", "notes": "second"}],
    )
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert not vault_path.exists()


def test_non_string_field_in_config_is_an_error(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare(
        gen,
        tmp_path,
        monkeypatch,
        entries=[{"title": "a", "notes": "n", "password": 123}],
    )
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert not vault_path.exists()


def test_subgroup_entry_does_not_satisfy_root_lookup(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The flat structure looks only at the root group: an entry nested in a
    # subgroup is not considered present, so the root entry is added.
    _prepare(gen, tmp_path, monkeypatch)
    vault_path = tmp_path / "vault.kdbx"
    create_database(str(vault_path), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    core = kp.add_group(kp.root_group, "core")
    kp.add_entry(core, "password_salt", "user", "nested-value")
    kp.save()
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    kp2 = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    root_salt = kp2.find_entries(
        title="password_salt", group=kp2.root_group, recursive=False, first=True
    )
    assert root_salt is not None
    nested = kp2.find_entries(title="password_salt", recursive=True, first=True)
    assert nested is not None


def test_future_fields_applied_one_to_one(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Any config field that is a KeePass field name lands in the entry
    # verbatim, so future fields like username need no script change.
    _prepare(
        gen,
        tmp_path,
        monkeypatch,
        entries=[
            {
                "title": "service",
                "username": "svc-user",
                "password": "svc-pass",
                "notes": "Service credentials.",
            }
        ],
    )
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_OK
    kp = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    entry = kp.find_entries(
        title="service", group=kp.root_group, recursive=False, first=True
    )
    assert entry is not None
    assert entry.username == "svc-user"
    assert entry.password == "svc-pass"
    assert entry.notes == "Service credentials."


def test_url_field_is_rejected(
    gen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # url is not a structure field: it is maintained directly in the vault
    # databases, so a config that carries it must be rejected.
    _prepare(
        gen,
        tmp_path,
        monkeypatch,
        entries=[
            {
                "title": "google_script_key",
                "url": "https://example.com/exec",
                "notes": "Auth key.",
            }
        ],
    )
    vault_path = tmp_path / "default.vault"
    assert gen.main([str(vault_path)]) == gen.EXIT_ERROR
    assert not vault_path.exists()


def test_help_without_arguments(
    gen: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    # With no arguments the script prints its usage help and exits cleanly.
    assert gen.main([]) == gen.EXIT_OK
    assert "usage" in capsys.readouterr().out.lower()
