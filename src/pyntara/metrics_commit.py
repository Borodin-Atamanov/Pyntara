"""System Metrics queue ingest: move spool files into the queue.

The ingest step is the single bridge between the commit command and the
System Metrics queue: it moves every file published into the configured
spool_dir into the main_outbox directory of the queue. The deployed
ingest service (system_metrics-ingest.service, started by the
system_metrics-ingest.path unit whenever a file appears in the spool)
runs this step as root, so the strict queue modes apply. Every action is
journaled through the shared pyntara.logger helpers. The queue entry
keeps the original name plus a random alphanumeric suffix, the strict
queue file mode and the modification time of the spool file, which the
commit command sets to the commit time; the source spool file is removed
after publication (docs/spec/system-metrics.md, section Queue
architecture).
"""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import string
from pathlib import Path

from pyntara.config import Config
from pyntara.logger import log_progress as _log

# Characters of the random entry-name suffix: letters and digits.
_SUFFIX_ALPHABET = string.ascii_letters + string.digits

# Publication attempts before giving up on a unique queue name.
_LINK_ATTEMPTS = 5


def build_queue_name(original_name: str, suffix: str) -> str:
    """Queue entry name: the original name plus the suffix after a dot."""

    return f"{original_name}.{suffix}"


def restore_original_name(queue_name: str, suffix_length: int) -> str:
    """Strip the dot and the random suffix from a queue entry name.

    The suffix has a fixed configured length, so the original name is
    recovered by removing exactly the last suffix_length + 1 characters.
    """

    return queue_name[: -(suffix_length + 1)]


def _random_suffix(length: int) -> str:
    """Random suffix of the given length from letters and digits."""

    return "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(length))


def _queue_dirs(cfg: Config) -> tuple[Path, Path, Path]:
    """The queue root, main_outbox and temp directories from the config."""

    metrics = cfg.system_metrics_setup
    root = metrics.system_metrics_dir
    return root, root / metrics.main_outbox_dir, root / metrics.temp_dir


def _ensure_dirs(root: Path, outbox: Path, temp: Path, mode: int) -> None:
    """Create the queue directories with the configured strict mode."""

    for directory in (root, outbox, temp):
        directory.mkdir(mode=mode, parents=True, exist_ok=True)


def ingest_spool(cfg: Config) -> None:
    """Move every spool file into the queue; log each action.

    Each spool entry that is a regular non-empty file no larger than
    max_queue_file_size_bytes is published into main_outbox and then
    removed from the spool. Entries with the spool_temp_prefix are the
    commit command temporaries and are skipped. Rejected entries (not
    regular, empty, oversized) are removed from the spool and reported
    in the journal; a failed publication leaves the spool entry in place
    so the next ingest run retries it.
    """

    metrics = cfg.system_metrics_setup
    spool_dir = metrics.spool_dir
    root, outbox, temp = _queue_dirs(cfg)
    _ensure_dirs(root, outbox, temp, metrics.system_metrics_dir_mode)
    if not spool_dir.is_dir():
        _log(f"ingesting spool {spool_dir}: directory missing, nothing to do")
        return
    for entry in sorted(spool_dir.iterdir()):
        if entry.name.startswith(metrics.spool_temp_prefix):
            continue
        reason = _reject_reason(entry, metrics.max_queue_file_size_bytes)
        if reason is not None:
            _log(
                f"ingesting spool entry {entry}: {reason}, removing",
                priority=3,
            )
            try:
                entry.unlink(missing_ok=True)
            except OSError as exc:
                _log(
                    f"ingesting spool entry {entry}: cannot remove it: {exc}",
                    priority=3,
                )
            continue
        _publish_entry(
            entry,
            outbox,
            temp,
            metrics.queue_file_mode,
            metrics.queue_file_suffix_length,
        )


def _reject_reason(entry: Path, limit: int) -> str | None:
    """Why the spool entry must be rejected, or None when it is valid.

    Stat errors, non-regular files, empty files and files larger than
    the limit are all rejections; the returned reason is journaled before
    the entry is removed.
    """

    try:
        entry_stat = entry.stat()
    except OSError as exc:
        return f"cannot stat: {exc}"
    if not stat.S_ISREG(entry_stat.st_mode):
        return "not a regular file"
    if entry_stat.st_size == 0:
        return "empty"
    if entry_stat.st_size > limit:
        return (
            f"{entry_stat.st_size} bytes, larger than the limit of {limit} bytes"
        )
    return None


def _publish_entry(
    entry: Path,
    outbox: Path,
    temp: Path,
    file_mode: int,
    suffix_length: int,
) -> None:
    """Publish one spool entry into the queue and remove it from the spool.

    The entry is copied into the queue temp directory, given the queue
    file mode and the modification time of the spool entry (the commit
    time set by the commit command), published into main_outbox under
    the original name plus a random suffix through a hard link and then
    removed from the spool. A queue name collision tries another suffix.
    On any failure the spool entry is left in place so the next ingest
    run retries it; every successful ingest is journaled.
    """

    commit_time = entry.stat().st_mtime
    temp_path = temp / f".ingest-{secrets.token_hex(8)}"
    try:
        shutil.copy2(entry, temp_path)
        os.chmod(temp_path, file_mode)
        commit_time_ns = int(commit_time * 1_000_000_000)
        os.utime(temp_path, ns=(commit_time_ns, commit_time_ns))
        for _ in range(_LINK_ATTEMPTS):
            queue_name = build_queue_name(entry.name, _random_suffix(suffix_length))
            entry_path = outbox / queue_name
            try:
                os.link(temp_path, entry_path)
            except FileExistsError:
                # The random suffix collided with an existing entry; try
                # another suffix instead of overwriting anything.
                continue
            temp_path.unlink(missing_ok=True)
            entry.unlink(missing_ok=True)
            _log(f"ingested spool entry {entry} into {entry_path}")
            return
        _log(
            f"ingesting spool entry {entry}: cannot allocate a unique queue "
            f"name after {_LINK_ATTEMPTS} attempts, leaving it",
            priority=3,
        )
    except OSError as exc:
        _log(
            f"ingesting spool entry {entry}: failed: {exc}, leaving it",
            priority=3,
        )
    finally:
        temp_path.unlink(missing_ok=True)
