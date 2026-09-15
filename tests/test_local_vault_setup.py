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


def _create_source_vault(
    path: Path, password: str, *, local_password: str | None = LOCAL_PASSWORD
) -> None:
    """Create a source vault with the local password entry in the root group."""

    create_database(str(path), password=password)
    kp = PyKeePass(str(path), password=password)
    if local_password is not None:
        kp.add_entry(kp.root_group, ENTRY_TITLE, "pyntara", local_password)
    kp.save()


def _ctx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    vault_password: str | None = "prod-pass",
    force: bool = False,
    owner_uid: int = 0,
    owner_gid: int = 0,
) -> Context:
    """Context whose source vaults live in the temporary directory."""

    config = make_config(
        task_data_root=tmp_path,
        root_owner_uid=owner_uid,
        root_owner_gid=owner_gid,
        local_vault_source_production=Path("production.vault"),
        local_vault_source_default=Path("default.vault"),
        local_vault_path=tmp_path / "secrets" / "pyntara.vault",
        local_vault_pass_file_path=tmp_path / "etc" / "pass",
    )
    return make_context(
        task_name="local_vault_setup",
        vault_password=vault_password,
        force_tasks=frozenset({"local_vault_setup"}) if force else frozenset(),
        repo_root=tmp_path,
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


def test_password_file_writable_mode_comes_from_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An existing read-only password file is made writable with the
    # configured mode before the rewrite, so a stricter or looser value is
    # answered in the config and not in the code.
    seen: list[int] = []
    monkeypatch.setattr(
        local_vault_setup.os, "chmod", lambda path, mode: seen.append(mode)
    )
    pass_file = tmp_path / "pass"
    pass_file.write_text("old", encoding="utf-8")
    local_vault_setup._write_password_file(
        "new", pass_file, 0o700, 0o400, pass_file_writable_mode=0o640
    )
    assert seen == [0o700, 0o640, 0o400]
    assert pass_file.read_text(encoding="utf-8") == "new"


def test_skips_when_runtime_vault_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Without force an existing runtime vault that already carries every
    # source entry is left untouched.
    _create_source_vault(tmp_path / "production.vault", "prod-pass")
    local_vault = tmp_path / "secrets" / "pyntara.vault"
    pass_file = tmp_path / "etc" / "pass"
    local_vault.parent.mkdir(parents=True)
    pass_file.parent.mkdir(parents=True)
    create_database(str(local_vault), password="existing-pass")
    kp = PyKeePass(str(local_vault), password="existing-pass")
    kp.add_entry(kp.root_group, ENTRY_TITLE, "pyntara", "existing-pass")
    kp.save()
    pass_file.write_text("existing-pass", encoding="utf-8")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert "already exists" in (result.message or "")
    assert pass_file.read_text(encoding="utf-8") == "existing-pass"


def test_syncs_missing_source_entries_into_existing_runtime_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A runtime vault created before the telemetry password entry existed
    # gains it from the source vault on a normal run, without a rewrite.
    source = tmp_path / "production.vault"
    _create_source_vault(source, "prod-pass")
    source_kp = PyKeePass(str(source), password="prod-pass")
    source_kp.add_entry(source_kp.root_group, "telemetry_password", "", "tele-secret")
    source_kp.save()
    local_vault = tmp_path / "secrets" / "pyntara.vault"
    pass_file = tmp_path / "etc" / "pass"
    local_vault.parent.mkdir(parents=True)
    pass_file.parent.mkdir(parents=True)
    create_database(str(local_vault), password="local-pass")
    kp = PyKeePass(str(local_vault), password="local-pass")
    kp.add_entry(kp.root_group, ENTRY_TITLE, "pyntara", "local-pass")
    kp.save()
    pass_file.write_text("local-pass", encoding="utf-8")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    reopened = PyKeePass(str(local_vault), password="local-pass")
    entry = reopened.find_entries(title="telemetry_password", first=True)
    assert entry is not None
    assert entry.password == "tele-secret"


def test_syncs_missing_source_group_into_existing_runtime_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A runtime vault created before the port_forwarding_servers group
    # existed gains the whole group from the source vault on a normal run,
    # with its entries, and a second run then changes nothing.
    source = tmp_path / "production.vault"
    _create_source_vault(source, "prod-pass")
    source_kp = PyKeePass(str(source), password="prod-pass")
    group = source_kp.add_group(
        source_kp.root_group, "port_forwarding_servers", notes="servers"
    )
    source_kp.add_entry(
        group, "Server 001", "", "", url="169.58.51.98", notes=""
    )
    source_kp.save()
    local_vault = tmp_path / "secrets" / "pyntara.vault"
    pass_file = tmp_path / "etc" / "pass"
    local_vault.parent.mkdir(parents=True)
    pass_file.parent.mkdir(parents=True)
    create_database(str(local_vault), password="local-pass")
    kp = PyKeePass(str(local_vault), password="local-pass")
    kp.add_entry(kp.root_group, ENTRY_TITLE, "pyntara", "local-pass")
    kp.save()
    pass_file.write_text("local-pass", encoding="utf-8")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    reopened = PyKeePass(str(local_vault), password="local-pass")
    synced = reopened.find_groups(name="port_forwarding_servers", first=True)
    assert synced is not None
    assert [entry.title for entry in synced.entries] == ["Server 001"]
    assert synced.entries[0].url == "169.58.51.98"
    again = local_vault_setup.task(
        _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    )
    assert again.changed is False


def test_syncs_missing_group_entry_into_existing_runtime_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A runtime vault that already carries the group but lacks one of its
    # entries gains the missing entry from the source vault on a normal run.
    source = tmp_path / "production.vault"
    _create_source_vault(source, "prod-pass")
    source_kp = PyKeePass(str(source), password="prod-pass")
    group = source_kp.add_group(
        source_kp.root_group, "port_forwarding_servers", notes="servers"
    )
    source_kp.add_entry(
        group, "Server 001", "", "", url="169.58.51.98", notes=""
    )
    source_kp.save()
    local_vault = tmp_path / "secrets" / "pyntara.vault"
    pass_file = tmp_path / "etc" / "pass"
    local_vault.parent.mkdir(parents=True)
    pass_file.parent.mkdir(parents=True)
    create_database(str(local_vault), password="local-pass")
    kp = PyKeePass(str(local_vault), password="local-pass")
    kp.add_entry(kp.root_group, ENTRY_TITLE, "pyntara", "local-pass")
    kp.add_group(kp.root_group, "port_forwarding_servers", notes="servers")
    kp.save()
    pass_file.write_text("local-pass", encoding="utf-8")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert result.changed is True
    reopened = PyKeePass(str(local_vault), password="local-pass")
    synced = reopened.find_groups(name="port_forwarding_servers", first=True)
    assert synced is not None
    assert [entry.title for entry in synced.entries] == ["Server 001"]
    assert synced.entries[0].url == "169.58.51.98"


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


def test_warns_when_no_vault_opens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # When neither vault opens, the task reports the reason as a warning,
    # journals it at the configured error priority and does not raise; the
    # runtime vault is the only thing the task builds, so there is nothing
    # else to do.
    _create_source_vault(tmp_path / "production.vault", "other-pass")
    _create_source_vault(tmp_path / "default.vault", "another-pass")
    recorded: list[tuple[str, int]] = []

    def _recording_log(message: str, *, priority: int = 6) -> None:
        recorded.append((message, priority))

    monkeypatch.setattr(local_vault_setup, "_log", _recording_log)
    ctx = _ctx(monkeypatch, tmp_path, vault_password="wrong-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert any("source vault" in warning for warning in result.warnings)
    serious = [entry for entry in recorded if entry[1] == 3]
    assert serious, "the serious error must be journaled at priority 3"


def test_warns_when_entry_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A source vault without the local password entry is reported and no
    # runtime vault is written, because an empty password would be unsafe.
    _create_source_vault(
        tmp_path / "production.vault", "prod-pass", local_password=None
    )
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert any("missing or empty" in warning for warning in result.warnings)
    assert not ctx.config.local_vault_setup.local_vault_path.exists()


def test_warns_when_entry_nested_in_subgroup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The vault structure is flat: an entry inside a group is not part of
    # the structure and must not satisfy the root-group lookup.
    create_database(str(tmp_path / "production.vault"), password="prod-pass")
    kp = PyKeePass(str(tmp_path / "production.vault"), password="prod-pass")
    group = kp.add_group(kp.root_group, "core")
    kp.add_entry(group, ENTRY_TITLE, "pyntara", LOCAL_PASSWORD)
    kp.save()
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert any("missing or empty" in warning for warning in result.warnings)


def test_warns_when_entry_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty password value is reported: a vault with an empty password
    # would be unsafe, so nothing is written.
    _create_source_vault(tmp_path / "production.vault", "prod-pass", local_password="")
    ctx = _ctx(monkeypatch, tmp_path, vault_password="prod-pass")
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert any("missing or empty" in warning for warning in result.warnings)
    assert not ctx.config.local_vault_setup.local_vault_path.exists()


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


def test_owner_comes_from_the_engine_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another owner pair in the [engine] table is the pair the run applies,
    # so no module writes the owner of root itself.
    _create_source_vault(tmp_path / "production.vault", "prod-pass")
    monkeypatch.setattr(local_vault_setup.os, "geteuid", lambda: 0)
    chowned: list[tuple[object, int, int]] = []
    monkeypatch.setattr(
        local_vault_setup.os,
        "chown",
        lambda path, uid, gid: chowned.append((path, uid, gid)),
    )
    ctx = _ctx(
        monkeypatch, tmp_path, vault_password="prod-pass", owner_uid=7, owner_gid=11
    )
    result = local_vault_setup.task(ctx)
    assert result.success is True
    assert (tmp_path / "secrets" / "pyntara.vault", 7, 11) in chowned
    assert (tmp_path / "etc" / "pass", 7, 11) in chowned
