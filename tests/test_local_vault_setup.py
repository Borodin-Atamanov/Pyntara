"""Unit tests for the local_vault_setup task.

The tests create real KeePass databases in temporary directories with
pykeepass, so the re-encryption path is exercised for real: the runtime
vault must open with the local password and must not open with the source
password. All target paths come from a config built by support.make_config
and the repository root is monkeypatched to the temporary directory, so
the real vault files and system paths are never touched.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from pykeepass import PyKeePass, create_database
from pykeepass.exceptions import CredentialsError
from support import make_config, make_context

from pyntara.context import Context
from pyntara.tasks import local_vault_setup

LOCAL_PASSWORD = "local-secret-password"
ENTRY_TITLE = "pyntara_local_vault_password"
ENTRY_GROUP = "core"


def _create_source_vault(
    path: Path, password: str, *, local_password: str | None = LOCAL_PASSWORD
) -> None:
    """Create a source vault with the local password entry in group core."""

    create_database(str(path), password=password)
    kp = PyKeePass(str(path), password=password)
    if local_password is not None:
        group = kp.add_group(kp.root_group, ENTRY_GROUP)
        kp.add_entry(group, ENTRY_TITLE, "pyntara", local_password)
    kp.save()


def _ctx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    vault_password: str | None = "prod-pass",
    force: bool = False,
) -> Context:
    """Context whose source vaults live in the temporary directory."""

    monkeypatch.setattr(local_vault_setup, "REPO_ROOT", tmp_path)
    config = make_config(
        task_data_root=tmp_path,
        local_vault_source_production=Path("production.vault"),
        local_vault_source_default=Path("default.vault"),
        local_vault_path=tmp_path / "secrets" / "pyntara.vault",
        local_vault_pass_file_path=tmp_path / "etc" / "pass",
    )
    return make_context(
        vault_password=vault_password,
        force_tasks=frozenset({"local_vault_setup"}) if force else frozenset(),
        config=config,
    )


def _file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _opens_with(path: Path, password: str) -> bool:
    try:
        PyKeePass(str(path), password=password)
    except CredentialsError:
        return False
    return True


def test_creates_runtime_vault_and_password_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A fresh run must produce the runtime vault, the password file and the
    # fixed modes; the vault opens with the local password, not the source one.
    _create_source_vault(tmp_path / "production.vault", "prod-pass")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    local_vault = tmp_path / "secrets" / "pyntara.vault"
    pass_file = tmp_path / "etc" / "pass"
    assert local_vault.is_file()
    assert pass_file.is_file()
    assert _opens_with(local_vault, LOCAL_PASSWORD)
    assert not _opens_with(local_vault, "prod-pass")
    assert pass_file.read_text(encoding="utf-8") == LOCAL_PASSWORD
    assert _file_mode(local_vault.parent) == 0o700
    assert _file_mode(local_vault) == 0o640
    assert _file_mode(pass_file.parent) == 0o700
    assert _file_mode(pass_file) == 0o400


def test_skips_when_runtime_vault_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without force an existing runtime vault must be left untouched.
    _create_source_vault(tmp_path / "production.vault", "prod-pass")
    local_vault = tmp_path / "secrets" / "pyntara.vault"
    pass_file = tmp_path / "etc" / "pass"
    local_vault.parent.mkdir(parents=True)
    local_vault.write_bytes(b"existing-vault")
    pass_file.parent.mkdir(parents=True)
    pass_file.write_text("existing-pass", encoding="utf-8")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert "already exists" in (result.message or "")
    assert local_vault.read_bytes() == b"existing-vault"
    assert pass_file.read_text(encoding="utf-8") == "existing-pass"


def test_force_rewrites_runtime_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Force mode must rewrite the vault even when it exists, so a changed
    # local password in the source takes effect.
    _create_source_vault(
        tmp_path / "production.vault", "prod-pass", local_password="old-pass"
    )
    first_ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    assert local_vault_setup.task(first_ctx).changed is True
    local_vault = tmp_path / "secrets" / "pyntara.vault"
    _create_source_vault(
        tmp_path / "production.vault", "prod-pass", local_password="new-pass"
    )
    forced_ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass", force=True)
    result = local_vault_setup.task(forced_ctx)
    assert result.success is True
    assert result.changed is True
    assert _opens_with(local_vault, "new-pass")
    assert not _opens_with(local_vault, "old-pass")


def test_falls_back_to_default_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # When production does not open with the run password, the default vault
    # must be used as the source.
    _create_source_vault(tmp_path / "production.vault", "other-pass")
    _create_source_vault(tmp_path / "default.vault", "prod-pass")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert "default.vault" in (result.message or "")
    assert _opens_with(tmp_path / "secrets" / "pyntara.vault", LOCAL_PASSWORD)


def test_fails_when_no_vault_opens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # When neither vault opens, the task must fail with a journal error at
    # syslog level 3 and not raise.
    _create_source_vault(tmp_path / "production.vault", "other-pass")
    _create_source_vault(tmp_path / "default.vault", "another-pass")
    recorded: list[tuple[str, int]] = []

    def _recording_log(message: str, *, priority: int = 6) -> None:
        recorded.append((message, priority))

    monkeypatch.setattr(local_vault_setup, "_log", _recording_log)
    ctx = _ctx(monkeypatch, tmp_path, vault_password="wrong-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is False
    assert "source vault" in (result.error or "")
    serious = [entry for entry in recorded if entry[1] == 3]
    assert serious, "the serious error must be journaled at priority 3"


def test_fails_when_entry_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A source vault without the local password entry must fail the task.
    _create_source_vault(
        tmp_path / "production.vault", "prod-pass", local_password=None
    )
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is False
    assert "missing or empty" in (result.error or "")


def test_fails_when_entry_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty password value must fail the task: a vault with an empty
    # password would be unsafe.
    _create_source_vault(tmp_path / "production.vault", "prod-pass", local_password="")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is False
    assert "missing or empty" in (result.error or "")


def test_password_file_is_trimmed_without_trailing_newline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Surrounding whitespace of the password must be trimmed and no newline
    # appended: the file holds exactly the password.
    _create_source_vault(
        tmp_path / "production.vault", "prod-pass", local_password="  padded-pass  "
    )
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    pass_file = tmp_path / "etc" / "pass"
    content = pass_file.read_bytes()
    assert content == b"padded-pass"
    assert b"\n" not in content


def test_owner_set_to_root_when_running_as_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Under root the vault and the password file must be chowned to root:root.
    _create_source_vault(tmp_path / "production.vault", "prod-pass")
    monkeypatch.setattr(local_vault_setup.os, "geteuid", lambda: 0)
    chowned: list[tuple[object, int, int]] = []
    monkeypatch.setattr(
        local_vault_setup.os,
        "chown",
        lambda path, uid, gid: chowned.append((path, uid, gid)),
    )
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    local_vault = tmp_path / "secrets" / "pyntara.vault"
    pass_file = tmp_path / "etc" / "pass"
    assert (local_vault, 0, 0) in chowned
    assert (pass_file, 0, 0) in chowned
