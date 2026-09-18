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
from support import make_context

from pyntara.nextdns_profile import select_profile_from_vault
from pyntara.tasks import nextdns_setup_system_wide as task_module
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import nextdns_setup_system_wide as values

VAULT_PASSWORD = "local-vault-password"
PROFILE_IDS = ("39284e", "938263", "a47276", "b2e82c", "cb3874")


@pytest.fixture(autouse=True)
def _point_the_profile_id_file_at_the_temporary_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own profile ID file path.

    The path is a shared value now, so the fixture points it at the temporary
    directory of the test and the shipped value comes back afterwards.
    """

    monkeypatch.setattr(
        common_values,
        "PROFILE_ID_FILE_PATH",
        tmp_path / "var" / "lib" / "pyntara" / "nextdns_profile_id",
    )


def _ctx(
    tmp_path: Path,
    *,
    force: bool = False,
):
    """Context safe for unit tests; the real files are never touched."""

    return make_context(
        task_name="nextdns_setup_system_wide",
        install_mode="server",
        vault_password=VAULT_PASSWORD,
        force_tasks=frozenset({"nextdns_setup_system_wide"}) if force else frozenset(),
        repo_root=tmp_path,
        task_data_root=tmp_path,
    )



def _install_source_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create a production source vault with a NextDNS group and five profiles."""

    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault), password=VAULT_PASSWORD)
    group = kp.add_group(kp.root_group, "NextDNS", notes="test profiles")
    for profile_id in PROFILE_IDS:
        kp.add_entry(group, f"{profile_id} profile", profile_id, "")
    kp.save()


def _selected_profile(tmp_path: Path) -> str:
    """The profile the task derives for the pinned hostname."""

    vault_path = tmp_path / "secrets" / "production.vault"
    kp = PyKeePass(str(vault_path), password=VAULT_PASSWORD)
    selected = select_profile_from_vault(kp, values.VAULT_GROUP_TITLE)
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
    profile_file = common_values.PROFILE_ID_FILE_PATH
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
    monkeypatch.setattr(engine_values, "ROOT_OWNER_UID", 7)
    monkeypatch.setattr(engine_values, "ROOT_OWNER_GID", 11)
    ctx = _ctx(tmp_path)
    result = task_module.task(ctx)
    assert result.success is True
    profile_file = common_values.PROFILE_ID_FILE_PATH
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
    selected = _selected_profile(tmp_path)
    profile_file = common_values.PROFILE_ID_FILE_PATH
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text(f"{selected}\n", encoding="utf-8")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert result.skipped is False
    assert result.message is not None
    assert "already carries" in result.message


def test_force_rewrites_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_source_vault(tmp_path, monkeypatch)
    ctx = _ctx(tmp_path, force=True)
    monkeypatch.setattr(socket, "gethostname", lambda: "pyntara-test-host")
    selected = _selected_profile(tmp_path)
    profile_file = common_values.PROFILE_ID_FILE_PATH
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text(f"{selected}\n", encoding="utf-8")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True


def test_missing_group_warns_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The vault holds no group for the profiles: the reason is a warning
    # of a completed task, because writing the profile ID is the only step
    # of the task and it cannot run.
    vault = tmp_path / "secrets" / "production.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    ctx = _ctx(tmp_path)
    result = task_module.task(ctx)
    assert result.success is True
    assert any("group" in warning for warning in result.warnings)
    assert not common_values.PROFILE_ID_FILE_PATH.exists()


def test_empty_group_warns_without_writing(
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
    assert result.success is True
    assert result.warnings
    assert not common_values.PROFILE_ID_FILE_PATH.exists()
