"""Unit tests for the System Metrics dispatcher and the Google channel sender.

The dispatcher is exercised against temporary directories, so no system
paths are touched. The sender uploads through a recorded fake of
run_command, so no real curl process and no real network request is ever
made; the runtime vault is created for real with pykeepass, so the
credential path is exercised end to end (developer guide).
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import pytest
from pykeepass import PyKeePass, create_database
from support import FakeProc, make_config

from pyntara.config import Config
from pyntara.metrics_send import dispatch_entries, send_google_queue

SUFFIX_LENGTH = 12
URL = "https://script.google.com/macros/s/abcdefghijklmnopqrstuvwxyz/exec"
AUTH_KEY = "shared-auth-key"
VAULT_PASSWORD = "local-vault-password"


def _send_config(tmp_path: Path, **kwargs: object) -> Config:
    """Config whose queue, vault and password file live in the temporary dir."""

    return make_config(
        task_data_root=tmp_path,
        system_metrics_dir=tmp_path / "metrics",
        local_vault_path=tmp_path / "secrets" / "pyntara.vault",
        local_vault_pass_file_path=tmp_path / "etc" / "pass",
        **kwargs,
    )


def _install_vault(tmp_path: Path) -> None:
    """Create a real runtime vault with a google_script_key entry."""

    vault = tmp_path / "secrets" / "pyntara.vault"
    vault.parent.mkdir(parents=True)
    create_database(str(vault), password=VAULT_PASSWORD)
    kp = PyKeePass(str(vault), password=VAULT_PASSWORD)
    kp.add_entry(
        kp.root_group,
        title="google_script_key",
        username="script-id",
        password=AUTH_KEY,
        url=URL,
    )
    kp.save()
    pass_file = tmp_path / "etc" / "pass"
    pass_file.parent.mkdir(parents=True)
    pass_file.write_text(VAULT_PASSWORD, encoding="utf-8")


def _make_entry(channel: Path, name: str, body: str, mtime: float) -> Path:
    """Create one channel entry with the configured name and commit time."""

    channel.mkdir(parents=True, exist_ok=True)
    entry = channel / f"{name}.0123456789ab"
    entry.write_text(body, encoding="utf-8")
    os.utime(entry, (mtime, mtime))
    return entry


def _fake_curl(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "OK sent",
    returncode: int = 0,
) -> list[tuple[list[str], dict[str, object]]]:
    """Replace run_command with a recorder returning the given response."""

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> FakeProc:
        calls.append((list(command), kwargs))
        return FakeProc(returncode=returncode, stdout=stdout)

    monkeypatch.setattr("pyntara.metrics_send.run_command", fake_run)
    return calls


def _arg(call: tuple[list[str], dict[str, object]], prefix: str) -> str:
    """The value of the first command argument starting with the prefix."""

    for arg in call[0]:
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    raise AssertionError(f"no argument starting with {prefix!r} in {call[0]}")


def test_dispatch_links_entry_into_channel_and_removes_from_outbox(
    tmp_path: Path,
) -> None:
    # One outbox entry appears in the channel as a hard link with the same
    # name and inode, and the name is removed from main_outbox.
    cfg = _send_config(tmp_path)
    outbox = tmp_path / "metrics" / "main_outbox"
    outbox.mkdir(parents=True)
    entry = outbox / "report.txt.abc123def456"
    entry.write_text("hello", encoding="utf-8")
    inode = entry.stat().st_ino
    dispatch_entries(cfg)
    channel = tmp_path / "metrics" / "google_script"
    names = list(channel.iterdir())
    assert len(names) == 1
    linked = names[0]
    assert linked.name == "report.txt.abc123def456"
    assert linked.read_text(encoding="utf-8") == "hello"
    assert linked.stat().st_ino == inode
    assert not entry.exists()


def test_dispatch_creates_channel_and_sent_dirs_with_mode(tmp_path: Path) -> None:
    # The channel queue and the sent archive appear with the strict queue
    # directory mode even when nothing is committed yet.
    cfg = _send_config(tmp_path)
    dispatch_entries(cfg)
    for directory in ("google_script", "main_sent"):
        path = tmp_path / "metrics" / directory
        assert path.is_dir()
        assert os.stat(path).st_mode & 0o777 == 0o700


def test_dispatch_failed_link_keeps_entry_in_outbox(tmp_path: Path) -> None:
    # A channel that cannot accept the link (a directory at the target
    # name) keeps the entry in main_outbox for the next cycle.
    cfg = _send_config(tmp_path)
    outbox = tmp_path / "metrics" / "main_outbox"
    outbox.mkdir(parents=True)
    entry = outbox / "report.txt.abc123def456"
    entry.write_text("hello", encoding="utf-8")
    channel = tmp_path / "metrics" / "google_script"
    channel.mkdir(parents=True)
    (channel / entry.name).mkdir()
    dispatch_entries(cfg)
    assert entry.exists()
    assert len(list(channel.iterdir())) == 1


def test_dispatch_second_run_does_not_duplicate(tmp_path: Path) -> None:
    # A second dispatch pass finds an empty main_outbox and leaves the
    # channel queue untouched.
    cfg = _send_config(tmp_path)
    outbox = tmp_path / "metrics" / "main_outbox"
    outbox.mkdir(parents=True)
    entry = outbox / "a.txt.abc123def456"
    entry.write_text("x", encoding="utf-8")
    dispatch_entries(cfg)
    dispatch_entries(cfg)
    channel = tmp_path / "metrics" / "google_script"
    assert len(list(channel.iterdir())) == 1


def test_send_uploads_original_name_key_and_base64_and_moves_to_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The upload carries the original name (the suffix stripped), the
    # shared auth key from the vault and the Base64 content; an OK
    # response moves the entry to main_sent.
    cfg = _send_config(tmp_path)
    _install_vault(tmp_path)
    calls = _fake_curl(monkeypatch)
    channel = tmp_path / "metrics" / "google_script"
    entry = _make_entry(channel, "report.txt", "hello", time.time())
    send_google_queue(cfg)
    assert len(calls) == 1
    command = calls[0][0]
    assert _arg(calls[0], "filename=") == "report.txt"
    assert _arg(calls[0], "pass=") == AUTH_KEY
    assert _arg(calls[0], "data=") == base64.b64encode(b"hello").decode()
    assert command[-1] == URL
    assert "--location" in command
    assert command[command.index("--request") + 1] == "POST"
    assert "--max-time" in command
    assert not entry.exists()
    sent = tmp_path / "metrics" / "main_sent"
    assert len(list(sent.iterdir())) == 1
    assert (sent / entry.name).read_text(encoding="utf-8") == "hello"


def test_send_error_response_keeps_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The web app rejects the upload: the entry stays for the next retry.
    cfg = _send_config(tmp_path)
    _install_vault(tmp_path)
    calls = _fake_curl(monkeypatch, stdout="ERROR: Unauthorized")
    channel = tmp_path / "metrics" / "google_script"
    entry = _make_entry(channel, "report.txt", "x", time.time())
    send_google_queue(cfg)
    assert len(calls) == 1
    assert entry.exists()
    assert not list((tmp_path / "metrics" / "main_sent").iterdir())


def test_send_curl_failure_keeps_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # curl fails (network error): the entry stays for the next retry.
    cfg = _send_config(tmp_path)
    _install_vault(tmp_path)
    calls = _fake_curl(monkeypatch, returncode=7)
    channel = tmp_path / "metrics" / "google_script"
    entry = _make_entry(channel, "report.txt", "x", time.time())
    send_google_queue(cfg)
    assert len(calls) == 1
    assert entry.exists()


def test_send_without_vault_skips_without_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No runtime vault: the drain is skipped and no upload is attempted.
    cfg = _send_config(tmp_path)
    calls = _fake_curl(monkeypatch)
    channel = tmp_path / "metrics" / "google_script"
    entry = _make_entry(channel, "report.txt", "x", time.time())
    send_google_queue(cfg)
    assert calls == []
    assert entry.exists()


def test_send_skips_empty_and_oversized_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Empty and oversized entries are journaled and skipped; the entry
    # within the limit is uploaded and archived.
    cfg = _send_config(tmp_path, system_metrics_max_queue_file_size_bytes=10)
    _install_vault(tmp_path)
    calls = _fake_curl(monkeypatch)
    channel = tmp_path / "metrics" / "google_script"
    empty = _make_entry(channel, "empty.txt", "", time.time())
    big = _make_entry(channel, "big.txt", "12345678901", time.time())
    good = _make_entry(channel, "good.txt", "1234567890", time.time())
    send_google_queue(cfg)
    assert len(calls) == 1
    assert _arg(calls[0], "filename=") == "good.txt"
    assert empty.exists()
    assert big.exists()
    assert not good.exists()


def test_send_oldest_first_uploads_in_commit_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # With the default order the earliest committed entry is uploaded
    # first, so the drain follows the commit order.
    cfg = _send_config(tmp_path, system_metrics_send_order="oldest_first")
    _install_vault(tmp_path)
    calls = _fake_curl(monkeypatch)
    channel = tmp_path / "metrics" / "google_script"
    now = time.time()
    _make_entry(channel, "first.txt", "1", now - 200)
    _make_entry(channel, "second.txt", "2", now - 100)
    _make_entry(channel, "third.txt", "3", now)
    send_google_queue(cfg)
    order = [_arg(call, "filename=") for call in calls]
    assert order == ["first.txt", "second.txt", "third.txt"]


def test_send_newest_first_uploads_newest_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # With newest_first the latest committed entry is uploaded first.
    cfg = _send_config(tmp_path, system_metrics_send_order="newest_first")
    _install_vault(tmp_path)
    calls = _fake_curl(monkeypatch)
    channel = tmp_path / "metrics" / "google_script"
    now = time.time()
    _make_entry(channel, "first.txt", "1", now - 200)
    _make_entry(channel, "second.txt", "2", now - 100)
    _make_entry(channel, "third.txt", "3", now)
    send_google_queue(cfg)
    order = [_arg(call, "filename=") for call in calls]
    assert order == ["third.txt", "second.txt", "first.txt"]
