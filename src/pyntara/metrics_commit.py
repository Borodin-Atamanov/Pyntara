"""System command commit_system_metrics: commit a file to the metrics queue.

The utility is the single write path of the System Metrics queue: it
copies a regular file into the main_outbox directory of the queue with its
original name plus a random alphanumeric suffix, strict permissions and a
modification time equal to the commit time. The suffix makes entries with
identical original names coexist without collisions; the deployed service
strips it before upload, so the remote server receives the original name.
The source file is left untouched. The utility never encrypts, reads
secrets or sends anything: producers create their artifacts (encrypted
PDFs, logs) and only commit paths to the queue (docs/spec/system-metrics.md,
section Queue architecture).

The utility creates only system_metrics_dir, main_outbox and temp; the
channel queues and the sent archive are created by the deployed service.
Temporary files left in temp by a crash between the hard link and the
unlink are never swept (explicit decision, docs/spec/system-metrics.md).
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import stat
import string
import sys
import time
from pathlib import Path

from pyntara.config import Config, load_config

# Queue directory names are fixed machine contracts
# (docs/spec/system-metrics.md, section Queue architecture).
MAIN_OUTBOX = "main_outbox"
TEMP_DIR = "temp"

# The utility must know where to find the config before it can read it;
# the system path is a documented exception to the rule that behavioral
# values live in config.toml (docs/guides/project-rules.md section 4).
DEFAULT_CONFIG_PATH = "/etc/pyntara/config.toml"

# Characters of the random entry-name suffix: letters and digits.
_SUFFIX_ALPHABET = string.ascii_letters + string.digits

# Publication attempts before giving up on a unique queue name.
_LINK_ATTEMPTS = 5


class CommitError(RuntimeError):
    """Raised when a file cannot be committed to the queue."""


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

    root = cfg.system_metrics_setup.system_metrics_dir
    return root, root / MAIN_OUTBOX, root / TEMP_DIR


def _ensure_dirs(root: Path, outbox: Path, temp: Path, mode: int) -> None:
    """Create the queue directories with the configured strict mode."""

    for directory in (root, outbox, temp):
        directory.mkdir(mode=mode, parents=True, exist_ok=True)


def enqueue_file(cfg: Config, source: Path) -> Path:
    """Commit a copy of the source file to the queue; return the entry path.

    The source must be an existing non-empty regular file no larger than
    max_queue_file_size_bytes; symlinks and hard links are followed and
    treated as the files they point to. The copy is written into temp
    first, given the queue file mode and the commit time, then published
    into main_outbox under the original name plus a random suffix through
    a hard link; the temp name is removed afterwards. If the random
    suffix collides with an existing entry, a new suffix is tried. The
    source file is never modified. Raises CommitError on any problem.
    """

    try:
        source_stat = source.stat()
    except FileNotFoundError:
        raise CommitError(f"source file not found: {source}") from None
    except OSError as exc:
        raise CommitError(f"cannot stat source file {source}: {exc}") from exc
    if not stat.S_ISREG(source_stat.st_mode):
        raise CommitError(f"source is not a regular file: {source}")
    if source_stat.st_size == 0:
        raise CommitError(f"source file is empty: {source}")
    limit = cfg.system_metrics_setup.max_queue_file_size_bytes
    if source_stat.st_size > limit:
        raise CommitError(
            f"source file {source} is {source_stat.st_size} bytes, "
            f"larger than the limit of {limit} bytes"
        )

    root, outbox, temp = _queue_dirs(cfg)
    _ensure_dirs(root, outbox, temp, cfg.system_metrics_setup.system_metrics_dir_mode)

    file_mode = cfg.system_metrics_setup.queue_file_mode
    suffix_length = cfg.system_metrics_setup.queue_file_suffix_length
    temp_path = temp / f".commit-{secrets.token_hex(8)}"
    commit_time = time.time()
    try:
        shutil.copy2(source, temp_path)
        # copy2 preserves the source mode, which could be world-readable;
        # the queue entry must carry the strict configured mode, so the
        # mode and the commit time are set before publication.
        os.chmod(temp_path, file_mode)
        commit_time_ns = int(commit_time * 1_000_000_000)
        os.utime(temp_path, ns=(commit_time_ns, commit_time_ns))
        for _ in range(_LINK_ATTEMPTS):
            queue_name = build_queue_name(source.name, _random_suffix(suffix_length))
            entry_path = outbox / queue_name
            try:
                os.link(temp_path, entry_path)
            except FileExistsError:
                # The random suffix collided with an existing entry; try
                # another suffix instead of overwriting anything.
                continue
            temp_path.unlink(missing_ok=True)
            return entry_path
        raise CommitError(
            f"cannot allocate a unique queue name for {source.name} "
            f"after {_LINK_ATTEMPTS} attempts"
        )
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> None:
    """Parse the command line, load the config and commit the file.

    Exit codes: 0 on success with the entry path on stdout, 1 on any
    execution error with the message on stderr, 2 on usage errors from
    argparse. The utility does not write to the journal: it is a CLI for
    scripts and must not produce noise per commit.
    """

    parser = argparse.ArgumentParser(
        prog="commit_system_metrics",
        description="Commit a file to the System Metrics queue.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="path of the system config (default: %(default)s)",
    )
    parser.add_argument("file", help="path of the file to commit")
    args = parser.parse_args()
    try:
        cfg = load_config(Path(args.config))
    except Exception as exc:
        print(f"error: cannot load config: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    try:
        entry_path = enqueue_file(cfg, Path(args.file))
    except CommitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(entry_path)


if __name__ == "__main__":
    main()
