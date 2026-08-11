"""Unit tests for the System Metrics queue ingest.

The ingest is exercised against temporary directories, so no system
paths are touched. The random suffix is asserted by length and alphabet;
the exact value is not reproducible, so tests check the shape, not the
value.
"""

from __future__ import annotations

import os
import string
import time
from pathlib import Path

from support import make_config

from pyntara.config import Config
from pyntara.metrics_commit import (
    build_queue_name,
    ingest_spool,
    restore_original_name,
)

SUFFIX_LENGTH = 12
SUFFIX_ALPHABET = set(string.ascii_letters + string.digits)
OUTBOX = "main_outbox"
TEMP = "temp"


def _spool_config(tmp_path: Path, **kwargs: object) -> Config:
    """Config whose spool and metrics queue live in the temporary directory."""

    return make_config(
        system_metrics_dir=tmp_path / "metrics",
        system_metrics_spool_dir=tmp_path / "spool",
        **kwargs,
    )


def _spool_file(tmp_path: Path, name: str = "report.txt", body: str = "data") -> Path:
    """Create the spool directory and place one file in it."""

    spool = tmp_path / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    entry = spool / name
    entry.write_text(body, encoding="utf-8")
    return entry


def stat_mode(path: Path) -> int:
    """File mode bits of the path."""

    return os.stat(path).st_mode & 0o777


def test_ingest_moves_file_into_outbox_with_suffix(
    tmp_path: Path,
) -> None:
    # The spool file is moved into main_outbox under the original name
    # plus a random suffix of the configured length; the spool entry is
    # removed after the ingest.
    cfg = _spool_config(tmp_path)
    entry = _spool_file(tmp_path, "report.txt", "hello")
    ingest_spool(cfg)
    outbox = tmp_path / "metrics" / OUTBOX
    names = list(outbox.iterdir())
    assert len(names) == 1
    committed = names[0]
    assert committed.name.startswith("report.txt.")
    suffix = committed.name.rpartition(".")[2]
    assert len(suffix) == SUFFIX_LENGTH
    assert set(suffix) <= SUFFIX_ALPHABET
    assert committed.read_text(encoding="utf-8") == "hello"
    assert not entry.exists()


def test_queue_name_roundtrip_restores_original_name() -> None:
    # The queue name carries the original name verbatim, including dots,
    # and the reverse operation strips exactly the suffix.
    original = "report.2026.pdf"
    suffix = "a1b2c3d4e5f6"
    queue_name = build_queue_name(original, suffix)
    assert queue_name == "report.2026.pdf.a1b2c3d4e5f6"
    assert restore_original_name(queue_name, SUFFIX_LENGTH) == original


def test_ingest_strips_suffix_and_keeps_original_name(tmp_path: Path) -> None:
    # The committed entry carries the original name; the sender recovers
    # it by stripping exactly the suffix.
    cfg = _spool_config(tmp_path)
    _spool_file(tmp_path, "report.txt", "x")
    ingest_spool(cfg)
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    assert restore_original_name(committed.name, SUFFIX_LENGTH) == "report.txt"


def test_dirs_and_entry_carry_configured_modes(tmp_path: Path) -> None:
    # Every queue directory is 0700 and the entry is 0600, the strictest
    # modes, regardless of the spool entry mode.
    cfg = _spool_config(tmp_path)
    entry = _spool_file(tmp_path, "modes.txt", "x")
    os.chmod(entry, 0o644)
    ingest_spool(cfg)
    for directory in (
        tmp_path / "metrics",
        tmp_path / "metrics" / OUTBOX,
        tmp_path / "metrics" / TEMP,
    ):
        assert stat_mode(directory) == 0o700
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    assert stat_mode(committed) == 0o600


def test_entry_mtime_is_commit_time(tmp_path: Path) -> None:
    # The modification time of the entry equals the spool entry time,
    # which the commit command sets to the commit time: the queue order
    # is the commit order.
    cfg = _spool_config(tmp_path)
    entry = _spool_file(tmp_path, "time.txt", "x")
    old = time.time() - 3600
    os.utime(entry, (old, old))
    ingest_spool(cfg)
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    entry_mtime = os.stat(committed).st_mtime
    assert abs(entry_mtime - old) < 2


def test_temp_prefix_entries_are_skipped(tmp_path: Path) -> None:
    # The commit command temporaries carry the spool_temp_prefix and are
    # never ingested; a real entry is moved alongside them.
    cfg = _spool_config(tmp_path)
    _spool_file(tmp_path, "real.txt", "x")
    temp = tmp_path / "spool" / ".commit-abcdef"
    temp.write_text("partial", encoding="utf-8")
    ingest_spool(cfg)
    names = [path.name for path in (tmp_path / "metrics" / OUTBOX).iterdir()]
    assert len(names) == 1
    assert names[0].startswith("real.txt.")
    assert temp.exists()


def test_empty_file_is_rejected_and_removed(tmp_path: Path) -> None:
    # An empty spool entry never reaches the queue and is removed.
    cfg = _spool_config(tmp_path)
    entry = _spool_file(tmp_path, "empty.txt", "")
    ingest_spool(cfg)
    outbox = tmp_path / "metrics" / OUTBOX
    assert not outbox.exists() or not any(outbox.iterdir())
    assert not entry.exists()


def test_oversized_file_is_rejected_and_removed(tmp_path: Path) -> None:
    # A spool entry larger than the configured limit is rejected and
    # removed; an entry exactly at the limit is ingested.
    cfg = _spool_config(tmp_path, system_metrics_max_queue_file_size_bytes=10)
    too_big = _spool_file(tmp_path, "big.txt", "12345678901")
    ingest_spool(cfg)
    outbox = tmp_path / "metrics" / OUTBOX
    assert not outbox.exists() or not any(outbox.iterdir())
    assert not too_big.exists()
    at_limit = _spool_file(tmp_path, "limit.txt", "1234567890")
    ingest_spool(cfg)
    committed = next(outbox.iterdir())
    assert committed.read_text(encoding="utf-8") == "1234567890"
    assert not at_limit.exists()


def test_directory_in_spool_is_rejected_and_kept(tmp_path: Path) -> None:
    # A subdirectory inside the spool is not a regular file: it is
    # reported, never removed recursively and never ingested.
    cfg = _spool_config(tmp_path)
    spool = tmp_path / "spool"
    spool.mkdir(parents=True)
    nested = spool / "sub"
    nested.mkdir()
    ingest_spool(cfg)
    assert nested.is_dir()
    outbox = tmp_path / "metrics" / OUTBOX
    assert not outbox.exists() or not any(outbox.iterdir())


def test_symlink_spool_entry_commits_target_content(tmp_path: Path) -> None:
    # A symlink spool entry is treated as the file it points to: the
    # content of the target is committed, the name of the entry is used.
    cfg = _spool_config(tmp_path)
    spool = tmp_path / "spool"
    spool.mkdir(parents=True)
    target = tmp_path / "target.txt"
    target.write_text("through link", encoding="utf-8")
    link = spool / "alias.txt"
    os.symlink(target, link)
    ingest_spool(cfg)
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    assert committed.name.startswith("alias.txt.")
    assert committed.read_text(encoding="utf-8") == "through link"
    assert not link.exists()


def test_no_temp_files_left_in_queue_after_ingest(tmp_path: Path) -> None:
    cfg = _spool_config(tmp_path)
    _spool_file(tmp_path, "ok.txt", "x")
    ingest_spool(cfg)
    temp = tmp_path / "metrics" / TEMP
    assert not any(temp.iterdir())


def test_missing_spool_creates_queue_dirs(tmp_path: Path) -> None:
    # Without a spool directory there is nothing to ingest, but the queue
    # directories are still ensured, like the previous commit utility did.
    cfg = _spool_config(tmp_path)
    ingest_spool(cfg)
    assert (tmp_path / "metrics" / OUTBOX).is_dir()
    assert (tmp_path / "metrics" / TEMP).is_dir()
