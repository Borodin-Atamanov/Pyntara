"""Unit tests for the nextdns_setup_system_wide task.

All external resources (vault, hostname) are mocked via monkeypatch; the
tests only touch temporary fixtures (docs/guides/developer-guide.md). The
vault is a real KeePass database in a temporary directory with a NextDNS
group.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from pykeepass import PyKeePass, create_database
from support import make_config, make_context

from pyntara.nextdns_profile import select_profile_from_vault
from pyntara.tasks import nextdns_setup_system_wide as task_module

VAULT_PASSWORD = "local-vault-password"
PROFILE_IDS = ("39284e", "938263", "a47276", "b2e82c", "cb3874")


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
    owner_uid: int = 0,
    owner_gid: int = 0,
):
    """Context with the task config rooted in the temporary directory."""

    return make_context(
        task_name="nextdns_setup_system_wide",
        install_mode="server",
        vault_password=VAULT_PASSWORD,
        force_tasks=frozenset({"nextdns_setup_system_wide"}) if force else frozenset(),
        repo_root=tmp_path,
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            root_owner_uid=owner_uid,
            root_owner_gid=owner_gid,
            nextdns_vault_group_title="NextDNS",
            nextdns_profile_id_file_path=tmp_path
            / "var"
            / "lib"
            / "pyntara"
            / "nextdns_profile_id",
            nextdns_profile_id_file_mode=0o644,
            nextdns_error_priority=3,
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


def _selected_profile(tmp_path: Path, ctx) -> str:
    """The profile the task derives for the pinned hostname."""

    vault_path = tmp_path / "secrets" / "production.vault"
    kp = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    selected = select_profile_from_vault(
        kp, ctx.config.nextdns_setup_system_wide.vault_group_title
    )
    assert selected is not None
    return selected


def test_records_profile_id_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    profile_file = ctx.config.nextdns_setup_system_wide.profile_id_file_path
    assert profile_file.read_text(encoding="utf-8").strip() in PROFILE_IDS
    assert result.message is not None
    assert "NextDNS profile" in result.message
    assert "dnsproxy_setup" in result.message


def test_profile_file_owner_comes_from_the_engine_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The file gets the owner pair of the [engine] table through the shared
    # apply_owner helper, so no module writes the owner of root
    # itself; a non-root run applies no owner at all.
    _install_source_vault(tmp_path, monkeypatch)
    monkeypatch.setattr(task_module.os, "geteuid", lambda: 0)
    chowned: list[tuple[object, int, int]] = []
    monkeypatch.setattr(
        task_module.os,
        "chown",
        lambda path, uid, gid: chowned.append((path, uid, gid)),
    )
    ctx = _ctx(tmp_path, owner_uid=7, owner_gid=11)
    result = task_module.task(ctx)
    assert result.success is True
    profile_file = ctx.config.nextdns_setup_system_wide.profile_id_file_path
    assert (profile_file, 7, 11) in chowned


def test_already_done_when_file_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path)
    # The selected profile depends on the machine hostname, so the test
    # pins the hostname and writes exactly the profile the task derives.
    # The task has already fulfilled its mission, so it reports done with
    # no changes, never a skip.
    monkeypatch.setattr(socket, "gethostname", lambda: "pyntara-test-host")
    selected = _selected_profile(tmp_path, ctx)
    profile_file = ctx.config.nextdns_setup_system_wide.profile_id_file_path
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text(f"{selected}\n", encoding="utf-8")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.skipped is False
    assert result.message is not None
    assert "already carries" in result.message


def test_force_rewrites_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path, force=True)
    monkeypatch.setattr(socket, "gethostname", lambda: "pyntara-test-host")
    selected = _selected_profile(tmp_path, ctx)
    profile_file = ctx.config.nextdns_setup_system_wide.profile_id_file_path
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text(f"{selected}\n", encoding="utf-8")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True


def test_missing_group_fails_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    ctx = _ctx(tmp_path)
    result = task_module.task(ctx)
    assert result.success is False
    assert "group" in (result.error or "")
    assert not ctx.config.nextdns_setup_system_wide.profile_id_file_path.exists()


def test_empty_group_fails_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault), password=VAULT_PASSWORD)
    kp.add_group(kp.root_group, "NextDNS", notes="empty")
    kp.save()
    ctx = _ctx(tmp_path)
    result = task_module.task(ctx)
    assert result.success is False
    assert not ctx.config.nextdns_setup_system_wide.profile_id_file_path.exists()
