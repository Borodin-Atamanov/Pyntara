"""Unit tests for the runtime vault writer.

The tests create real KeePass databases in temporary directories, so the write
and the mode of the secret database are exercised for real. The path of the
runtime vault is a value of the task, so one autouse fixture points it at the
temporary directory of the test and the shipped paths stay untouched.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from pykeepass import PyKeePass, create_database

from pyntara import runtime_vault
from pyntara.values import local_vault_setup as values


@pytest.fixture(autouse=True)
def _point_the_values_at_the_temporary_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test of this file its own runtime vault path."""

    monkeypatch.setattr(
        values, "LOCAL_VAULT_PATH", tmp_path / "secrets" / "pyntara.vault"
    )
    monkeypatch.setattr(
        values,
        "LOCAL_VAULT_RESCUE_PATH",
        tmp_path / "secrets" / "pyntara.vault.damaged",
    )


def _file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _create_source_vault(tmp_path: Path) -> PyKeePass:
    source = tmp_path / "source.vault"
    create_database(str(source), password="source-pass")
    kp = PyKeePass(str(source), password="source-pass")
    kp.add_entry(kp.root_group, "telemetry_password", "", "tele-secret")
    kp.save()
    return PyKeePass(str(source), password="source-pass")


def test_write_runtime_vault_uses_the_declared_modes(tmp_path: Path) -> None:
    # The runtime vault carries the re-encrypted source vault, the declared
    # file mode and the declared directory mode, and no temporary file is
    # left behind next to it.
    kp = _create_source_vault(tmp_path)
    runtime_vault.write_runtime_vault(kp, "local-pass")
    target = values.LOCAL_VAULT_PATH
    assert target.is_file()
    assert _file_mode(target) == values.LOCAL_VAULT_FILE_MODE
    assert _file_mode(target.parent) == values.SECRETS_DIR_MODE
    reopened = PyKeePass(str(target), password="local-pass")
    assert reopened.find_entries(title="telemetry_password", first=True) is not None
    leftovers = [
        path.name
        for path in target.parent.iterdir()
        if path.name != target.name
    ]
    assert leftovers == []


def test_write_runtime_vault_keeps_the_previous_file_when_the_save_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The vault is written through a temporary file next to the target, so a
    # failure inside the save leaves the previous file in place and never a
    # truncated vault where the machine reads it.
    kp = _create_source_vault(tmp_path)
    target = values.LOCAL_VAULT_PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous-content")

    def _failing_save(self: PyKeePass, filename: str | None = None) -> None:
        assert filename is not None
        Path(filename).write_bytes(b"half-written")
        raise OSError("no space left on device")

    monkeypatch.setattr(PyKeePass, "save", _failing_save)
    with pytest.raises(OSError):
        runtime_vault.write_runtime_vault(kp, "local-pass")
    assert target.read_bytes() == b"previous-content"


def test_save_runtime_vault_restores_the_declared_mode(tmp_path: Path) -> None:
    # A save through the KeePass library writes a new file and gives it the
    # umask of the process, so the mode has to be applied again on every save;
    # without that the secrets of the machine become readable by every user.
    runtime_vault.write_runtime_vault(_create_source_vault(tmp_path), "local-pass")
    target = values.LOCAL_VAULT_PATH
    target.chmod(0o666)
    kp = PyKeePass(str(target), password="local-pass")
    kp.add_entry(kp.root_group, "rustdesk_password", "machine-1", "rustdesk-secret")
    runtime_vault.save_runtime_vault(kp)
    assert _file_mode(target) == values.LOCAL_VAULT_FILE_MODE
    reopened = PyKeePass(str(target), password="local-pass")
    assert reopened.find_entries(title="rustdesk_password", first=True) is not None
