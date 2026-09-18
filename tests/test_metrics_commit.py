"""Unit tests for the System Metrics queue ingest.

The ingest is exercised against temporary directories, so no system
paths are touched. The random suffix is asserted by length and alphabet;
the exact value is not reproducible, so tests check the shape, not the
value.
"""

from __future__ import annotations

import os
import shutil
import string
import time
from pathlib import Path
from typing import Any

import pytest

from pyntara import metrics_commit
from pyntara.metrics_commit import (
    build_queue_name,
    ingest_spool,
    restore_original_name,
)
from pyntara.values import engine as engine_values
from pyntara.values import system_metrics_setup as values

SUFFIX_LENGTH = 12
SUFFIX_ALPHABET = set(string.ascii_letters + string.digits)
OUTBOX = "main_outbox"
TEMP = "temp"


def _spool_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs: Any
) -> None:
    """Point the declared paths at the temporary directory.

    The queue root and the spool are declared values, so they are set on
    the module; every further keyword replaces one more declared value.
    """

    monkeypatch.setattr(values, "SYSTEM_METRICS_DIR", tmp_path / "metrics")
    monkeypatch.setattr(values, "SPOOL_DIR", tmp_path / "spool")
    for name, value in kwargs.items():
        monkeypatch.setattr(values, name.upper(), value)


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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The spool file is moved into main_outbox under the original name
    # plus a random suffix of the configured length; the spool entry is
    # removed after the ingest.
    _spool_config(monkeypatch, tmp_path)
    entry = _spool_file(tmp_path, "report.txt", "hello")
    ingest_spool()
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


def test_ingest_strips_suffix_and_keeps_original_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The committed entry carries the original name; the sender recovers
    # it by stripping exactly the suffix.
    _spool_config(monkeypatch, tmp_path)
    _spool_file(tmp_path, "report.txt", "x")
    ingest_spool()
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    assert restore_original_name(committed.name, SUFFIX_LENGTH) == "report.txt"


def test_dirs_and_entry_carry_configured_modes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Every queue directory is 0700 and the entry is 0600, the strictest
    # modes, regardless of the spool entry mode.
    _spool_config(monkeypatch, tmp_path)
    entry = _spool_file(tmp_path, "modes.txt", "x")
    os.chmod(entry, 0o644)
    ingest_spool()
    for directory in (
        tmp_path / "metrics",
        tmp_path / "metrics" / OUTBOX,
        tmp_path / "metrics" / TEMP,
    ):
        assert stat_mode(directory) == 0o700
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    assert stat_mode(committed) == 0o600


def test_suffix_alphabet_comes_from_the_declared_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The proof of the value: the random part of a queue name is drawn
    # from the alphabet of the table, so a queue whose names must avoid a
    # character is a declared value.
    _spool_config(
        monkeypatch,
        tmp_path,
        queue_file_suffix_alphabet="a",
        queue_file_suffix_length=6,
    )
    _spool_file(tmp_path, "alpha.txt", "x")
    ingest_spool()
    names = [path.name for path in (tmp_path / "metrics" / OUTBOX).iterdir()]
    assert names == ["alpha.txt.aaaaaa"]


def test_entry_mtime_is_commit_time(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The modification time of the entry equals the spool entry time,
    # which the commit command sets to the commit time: the queue order
    # is the commit order.
    _spool_config(monkeypatch, tmp_path)
    entry = _spool_file(tmp_path, "time.txt", "x")
    old = time.time() - 3600
    os.utime(entry, (old, old))
    ingest_spool()
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    entry_mtime = os.stat(committed).st_mtime
    assert abs(entry_mtime - old) < 2


def test_commit_time_uses_the_configured_nanosecond_factor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The proof of the value: the factor that turns the commit time into
    # the unit the kernel takes is a declared value, so another factor is
    # the modification time the entry receives.
    monkeypatch.setattr(engine_values, "NANOSECONDS_PER_SECOND", 1_000_000)
    _spool_config(monkeypatch, tmp_path)
    entry = _spool_file(tmp_path, "factor.txt", "x")
    commit_time = 1_700_000_000.25
    os.utime(entry, (commit_time, commit_time))
    ingest_spool()
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    expected = int(commit_time * 1_000_000)
    assert abs(os.stat(committed).st_mtime_ns - expected) < 1_000_000


def test_temp_name_length_comes_from_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The proof of the value: the temporary name the ingest gives a copy
    # carries the configured number of random bytes in hex, so a queue
    # whose names must be shorter or longer is a declared value.
    seen: list[str] = []
    real_copy = shutil.copy2

    def recording_copy(source: str | Path, target: str | Path) -> str:
        seen.append(Path(str(target)).name)
        return str(real_copy(source, target))

    monkeypatch.setattr(metrics_commit.shutil, "copy2", recording_copy)
    _spool_config(monkeypatch, tmp_path, temp_name_random_bytes=4)
    _spool_file(tmp_path, "entry.txt", "x")
    ingest_spool()
    assert seen
    assert len(seen[0].removeprefix(".ingest-")) == 8


def test_temp_prefix_entries_are_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The commit command temporaries carry the spool_temp_prefix and are
    # never ingested; a real entry is moved alongside them.
    _spool_config(monkeypatch, tmp_path)
    _spool_file(tmp_path, "real.txt", "x")
    temp = tmp_path / "spool" / ".commit-abcdef"
    temp.write_text("partial", encoding="utf-8")
    ingest_spool()
    names = [path.name for path in (tmp_path / "metrics" / OUTBOX).iterdir()]
    assert len(names) == 1
    assert names[0].startswith("real.txt.")
    assert temp.exists()


def test_empty_file_is_rejected_and_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # An empty spool entry never reaches the queue and is removed.
    _spool_config(monkeypatch, tmp_path)
    entry = _spool_file(tmp_path, "empty.txt", "")
    ingest_spool()
    outbox = tmp_path / "metrics" / OUTBOX
    assert not outbox.exists() or not any(outbox.iterdir())
    assert not entry.exists()


def test_rejected_entry_is_journaled_at_the_configured_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The proof of the value: another error level in the declared values is
    # the level of the line that reports a rejected spool entry.
    levels: list[int | None] = []
    monkeypatch.setattr(
        metrics_commit,
        "_log",
        lambda message, **kwargs: levels.append(kwargs.get("priority")),
    )
    monkeypatch.setattr(engine_values, "ERROR_PRIORITY", 5)
    _spool_config(monkeypatch, tmp_path)
    _spool_file(tmp_path, "empty.txt", "")
    ingest_spool()
    assert levels == [5]


def test_oversized_file_is_rejected_and_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A spool entry larger than the configured limit is rejected and
    # removed; an entry exactly at the limit is ingested.
    _spool_config(monkeypatch, tmp_path, max_queue_file_size_bytes=10)
    too_big = _spool_file(tmp_path, "big.txt", "12345678901")
    ingest_spool()
    outbox = tmp_path / "metrics" / OUTBOX
    assert not outbox.exists() or not any(outbox.iterdir())
    assert not too_big.exists()
    at_limit = _spool_file(tmp_path, "limit.txt", "1234567890")
    ingest_spool()
    committed = next(outbox.iterdir())
    assert committed.read_text(encoding="utf-8") == "1234567890"
    assert not at_limit.exists()


def test_directory_in_spool_is_rejected_and_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A subdirectory inside the spool is not a regular file: it is
    # reported, never removed recursively and never ingested.
    _spool_config(monkeypatch, tmp_path)
    spool = tmp_path / "spool"
    spool.mkdir(parents=True)
    nested = spool / "sub"
    nested.mkdir()
    ingest_spool()
    assert nested.is_dir()
    outbox = tmp_path / "metrics" / OUTBOX
    assert not outbox.exists() or not any(outbox.iterdir())


def test_symlink_spool_entry_commits_target_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A symlink spool entry is treated as the file it points to: the
    # content of the target is committed, the name of the entry is used.
    _spool_config(monkeypatch, tmp_path)
    spool = tmp_path / "spool"
    spool.mkdir(parents=True)
    target = tmp_path / "target.txt"
    target.write_text("through link", encoding="utf-8")
    link = spool / "alias.txt"
    os.symlink(target, link)
    ingest_spool()
    committed = next((tmp_path / "metrics" / OUTBOX).iterdir())
    assert committed.name.startswith("alias.txt.")
    assert committed.read_text(encoding="utf-8") == "through link"
    assert not link.exists()


def test_no_temp_files_left_in_queue_after_ingest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _spool_config(monkeypatch, tmp_path)
    _spool_file(tmp_path, "ok.txt", "x")
    ingest_spool()
    temp = tmp_path / "metrics" / TEMP
    assert not any(temp.iterdir())


def test_missing_spool_creates_queue_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Without a spool directory there is nothing to ingest, but the queue
    # directories are still ensured, like the previous commit utility did.
    _spool_config(monkeypatch, tmp_path)
    ingest_spool()
    assert (tmp_path / "metrics" / OUTBOX).is_dir()
    assert (tmp_path / "metrics" / TEMP).is_dir()
