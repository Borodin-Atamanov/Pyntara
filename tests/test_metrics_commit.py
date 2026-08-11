"""Unit tests for the commit_system_metrics queue utility.

The utility is exercised against temporary directories, so no system
paths are touched. The random suffix is asserted by length and alphabet;
the exact value is not reproducible, so tests check the shape, not the
value.
"""

from __future__ import annotations

import os
import string
import time
from pathlib import Path

import pytest
from support import make_config

from pyntara.config import Config
from pyntara.metrics_commit import (
    CommitError,
    build_queue_name,
    enqueue_file,
    main,
    restore_original_name,
)

SUFFIX_LENGTH = 12
SUFFIX_ALPHABET = set(string.ascii_letters + string.digits)
OUTBOX = "main_outbox"
TEMP = "temp"


def _queue_config(tmp_path: Path, **kwargs: object) -> Config:
    """Config whose metrics queue lives in the temporary directory."""

    return make_config(system_metrics_dir=tmp_path / "metrics", **kwargs)


def _write_source(tmp_path: Path, name: str = "report.txt", body: str = "data") -> Path:
    """Create a source file with the given name and content."""

    source = tmp_path / name
    source.write_text(body, encoding="utf-8")
    return source


def test_commit_copies_file_into_outbox_with_suffix(
    tmp_path: Path,
) -> None:
    # The source is copied into main_outbox under the original name plus a
    # random suffix of the configured length; the source is untouched and
    # the returned path is the queue entry.
    cfg = _queue_config(tmp_path)
    source = _write_source(tmp_path, "report.txt", "hello")
    entry = enqueue_file(cfg, source)
    assert entry.parent == tmp_path / "metrics" / OUTBOX
    assert entry.name.startswith("report.txt.")
    suffix = entry.name.rpartition(".")[2]
    assert len(suffix) == SUFFIX_LENGTH
    assert set(suffix) <= SUFFIX_ALPHABET
    assert entry.read_text(encoding="utf-8") == "hello"
    assert source.read_text(encoding="utf-8") == "hello"


def test_queue_name_roundtrip_restores_original_name() -> None:
    # The queue name carries the original name verbatim, including dots,
    # and the reverse operation strips exactly the suffix.
    original = "report.2026.pdf"
    suffix = "a1b2c3d4e5f6"
    queue_name = build_queue_name(original, suffix)
    assert queue_name == "report.2026.pdf.a1b2c3d4e5f6"
    assert restore_original_name(queue_name, SUFFIX_LENGTH) == original


def test_two_commits_of_same_name_coexist(tmp_path: Path) -> None:
    # Identical original names get different suffixes: both entries exist
    # and neither is overwritten.
    cfg = _queue_config(tmp_path)
    source = _write_source(tmp_path, "same.txt", "body")
    first = enqueue_file(cfg, source)
    second = enqueue_file(cfg, source)
    assert first.name != second.name
    outbox = tmp_path / "metrics" / OUTBOX
    assert sorted(path.name for path in outbox.iterdir()) == sorted(
        [first.name, second.name]
    )


def test_dirs_and_entry_carry_configured_modes(tmp_path: Path) -> None:
    # Every queue directory is 0700 and the entry is 0600, the strictest
    # modes, regardless of the source mode.
    cfg = _queue_config(tmp_path)
    source = _write_source(tmp_path, "modes.txt", "x")
    os.chmod(source, 0o644)
    enqueue_file(cfg, source)
    for directory in (
        tmp_path / "metrics",
        tmp_path / "metrics" / OUTBOX,
        tmp_path / "metrics" / TEMP,
    ):
        assert stat_mode(directory) == 0o700
    entries = list((tmp_path / "metrics" / OUTBOX).iterdir())
    assert len(entries) == 1
    assert stat_mode(entries[0]) == 0o600


def stat_mode(path: Path) -> int:
    """File mode bits of the path."""

    return os.stat(path).st_mode & 0o777


def test_entry_mtime_is_commit_time(tmp_path: Path) -> None:
    # The modification time of the entry is the commit time, not the
    # modification time of the source: the queue order is the commit
    # order.
    cfg = _queue_config(tmp_path)
    source = _write_source(tmp_path, "time.txt", "x")
    old = time.time() - 3600
    os.utime(source, (old, old))
    before = time.time()
    entry = enqueue_file(cfg, source)
    after = time.time()
    entry_mtime = os.stat(entry).st_mtime
    assert before <= entry_mtime <= after


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    # An empty source never reaches the queue.
    cfg = _queue_config(tmp_path)
    source = _write_source(tmp_path, "empty.txt", "")
    with pytest.raises(CommitError, match="empty"):
        enqueue_file(cfg, source)
    outbox = tmp_path / "metrics" / OUTBOX
    assert not outbox.exists() or not any(outbox.iterdir())


def test_oversized_file_is_rejected(tmp_path: Path) -> None:
    # A file larger than the configured limit is rejected; a file exactly
    # at the limit passes.
    cfg = _queue_config(tmp_path, system_metrics_max_queue_file_size_bytes=10)
    too_big = _write_source(tmp_path, "big.txt", "12345678901")
    with pytest.raises(CommitError, match="limit"):
        enqueue_file(cfg, too_big)
    at_limit = _write_source(tmp_path, "limit.txt", "1234567890")
    assert enqueue_file(cfg, at_limit).is_file()


def test_missing_source_is_rejected(tmp_path: Path) -> None:
    cfg = _queue_config(tmp_path)
    with pytest.raises(CommitError, match="not found"):
        enqueue_file(cfg, tmp_path / "missing.txt")


def test_directory_source_is_rejected(tmp_path: Path) -> None:
    cfg = _queue_config(tmp_path)
    with pytest.raises(CommitError, match="not a regular file"):
        enqueue_file(cfg, tmp_path)


def test_dot_name_is_preserved(tmp_path: Path) -> None:
    # Hidden source names are committed unfiltered.
    cfg = _queue_config(tmp_path)
    source = _write_source(tmp_path, ".hidden", "x")
    entry = enqueue_file(cfg, source)
    assert entry.name.startswith(".hidden.")


def test_symlink_source_commits_target_content(tmp_path: Path) -> None:
    # A symlink source is treated as the file it points to: the content of
    # the target is committed, the name of the passed path is used.
    cfg = _queue_config(tmp_path)
    target = _write_source(tmp_path, "target.txt", "through link")
    link = tmp_path / "alias.txt"
    os.symlink(target, link)
    entry = enqueue_file(cfg, link)
    assert entry.name.startswith("alias.txt.")
    assert entry.read_text(encoding="utf-8") == "through link"


def test_no_temp_files_left_after_success(tmp_path: Path) -> None:
    cfg = _queue_config(tmp_path)
    source = _write_source(tmp_path, "ok.txt", "x")
    enqueue_file(cfg, source)
    temp = tmp_path / "metrics" / TEMP
    assert not any(temp.iterdir())


def test_no_temp_files_left_after_error(tmp_path: Path) -> None:
    cfg = _queue_config(tmp_path)
    with pytest.raises(CommitError):
        enqueue_file(cfg, tmp_path / "missing.txt")
    temp = tmp_path / "metrics" / TEMP
    assert not temp.exists() or not any(temp.iterdir())


def test_main_success_prints_entry_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _queue_config(tmp_path)
    entry = tmp_path / "metrics" / OUTBOX / "x.pdf.abcdefghijkl"
    monkeypatch.setattr("pyntara.metrics_commit.load_config", lambda path: cfg)
    monkeypatch.setattr(
        "pyntara.metrics_commit.enqueue_file", lambda cfg_arg, source: entry
    )
    monkeypatch.setattr(
        "sys.argv", ["commit_system_metrics", str(tmp_path / "x.pdf")]
    )
    main()
    assert capsys.readouterr().out.strip() == str(entry)


def test_main_uses_default_config_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _queue_config(tmp_path)
    seen: list[Path] = []

    def fake_load(path: Path) -> Config:
        seen.append(Path(path))
        return cfg

    monkeypatch.setattr("pyntara.metrics_commit.load_config", fake_load)
    monkeypatch.setattr(
        "pyntara.metrics_commit.enqueue_file",
        lambda cfg_arg, source: tmp_path / "entry",
    )
    monkeypatch.setattr("sys.argv", ["commit_system_metrics", "file.txt"])
    main()
    assert seen == [Path("/etc/pyntara/config.toml")]


def test_main_accepts_config_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _queue_config(tmp_path)
    config_path = tmp_path / "custom.toml"
    seen: list[Path] = []

    def fake_load(path: Path) -> Config:
        seen.append(Path(path))
        return cfg

    monkeypatch.setattr("pyntara.metrics_commit.load_config", fake_load)
    monkeypatch.setattr(
        "pyntara.metrics_commit.enqueue_file",
        lambda cfg_arg, source: tmp_path / "entry",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["commit_system_metrics", "--config", str(config_path), "file.txt"],
    )
    main()
    assert seen == [config_path]


def test_main_missing_file_argument_exits_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("sys.argv", ["commit_system_metrics"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_main_error_exits_one_with_stderr_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr("pyntara.metrics_commit.load_config", lambda path: cfg)

    def fail(cfg_arg: Config, source: Path) -> Path:
        del cfg_arg, source
        raise CommitError("boom")

    monkeypatch.setattr("pyntara.metrics_commit.enqueue_file", fail)
    monkeypatch.setattr("sys.argv", ["commit_system_metrics", "file.txt"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "boom" in capsys.readouterr().err
