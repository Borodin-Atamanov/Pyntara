"""System Metrics queue dispatch and Google Drive channel sending.

The dispatcher fans every main_outbox entry out into the channel queues:
one hard link per channel directory, and only after every link succeeds
is the name removed from main_outbox, so a channel enabled later receives
only entries committed after its enablement (docs/spec/system-metrics.md,
section Queue architecture). The Google Drive channel sender drains its
queue into the web app deployed from
task_data/system_metrics_setup/google_drive_script.js: every entry is
uploaded with curl, the original name (the random suffix stripped), the
shared auth key from the runtime vault and the Base64 content; an OK
response moves the entry to main_sent, every other outcome keeps it for
the next retry. The service loop pyntara.metrics.main runs the dispatcher
and the senders every cycle; each channel drains independently, so a
failure in one never stops the others.

The module imports pyntara.metrics by module and reads open_runtime_vault
through the attribute: the two modules import each other, so the access
is deferred to call time, when both modules are fully loaded.
"""

from __future__ import annotations

import base64
import os
import stat
import subprocess
from pathlib import Path

import pyntara.metrics
from pyntara.config import Config
from pyntara.logger import log_progress as _log
from pyntara.metrics_commit import restore_original_name
from pyntara.utils import run_command


def dispatch_entries(cfg: Config) -> None:
    """Link every main_outbox entry into every channel queue.

    The channel queues and the sent archive are ensured with the
    configured queue directory mode. For each main_outbox entry a hard
    link is created in every channel directory; only when all links
    succeeded is the name removed from main_outbox, so a channel that is
    enabled later receives only entries committed after its enablement.
    A failed link journals the error, removes the links already created
    for the entry in other channels and keeps the entry in main_outbox
    for the next cycle.
    """

    metrics = cfg.system_metrics_setup
    root = metrics.system_metrics_dir
    outbox = root / metrics.main_outbox_dir
    channels = [root / metrics.google_script_dir]
    sent = root / metrics.main_sent_dir
    for directory in (outbox, sent, *channels):
        directory.mkdir(
            mode=metrics.system_metrics_dir_mode, parents=True, exist_ok=True
        )
    for entry in sorted(outbox.iterdir()):
        linked: list[Path] = []
        for channel in channels:
            target = channel / entry.name
            try:
                os.link(entry, target)
            except OSError as exc:
                _log(
                    f"dispatching {entry.name} into {channel}: failed: {exc}, "
                    "keeping it",
                    priority=3,
                )
                for created in linked:
                    created.unlink(missing_ok=True)
                break
            linked.append(target)
        else:
            entry.unlink(missing_ok=True)
            _log(f"dispatched {entry.name} into the channel queues")


def send_google_queue(cfg: Config) -> None:
    """Drain the Google Drive channel queue into the web app.

    Every regular non-empty entry no larger than the configured limit is
    uploaded with curl: the original name (the random suffix stripped),
    the shared auth key from the runtime vault and the Base64 content.
    An OK response moves the entry to main_sent; an ERROR response, a
    curl failure or missing credentials keep every entry for the next
    cycle. Each entry is handled independently, so one failure never
    stops the drain.
    """

    metrics = cfg.system_metrics_setup
    channel = metrics.system_metrics_dir / metrics.google_script_dir
    sent = metrics.system_metrics_dir / metrics.main_sent_dir
    sent.mkdir(mode=metrics.system_metrics_dir_mode, parents=True, exist_ok=True)
    if not channel.is_dir():
        _log(f"google script channel: queue {channel} missing, skipping")
        return
    credentials = _google_script_credentials(cfg)
    if credentials is None:
        _log("google script channel: no credentials, skipping the drain")
        return
    url, key = credentials
    for entry in _ordered_entries(channel, metrics.send_order):
        if not _entry_uploadable(entry, metrics.max_queue_file_size_bytes):
            continue
        _send_entry(cfg, entry, url, key, sent)


def _google_script_credentials(cfg: Config) -> tuple[str, str] | None:
    """The Google web app url and auth key from the runtime vault, or None.

    The entry whose title comes from system_metrics_setup
    .google_script_key_entry_title carries the web app endpoint in the
    url field and the shared auth key in the password field
    (docs/spec/secrets-model.md). A vault that does not open, a missing
    entry or an empty field are journaled and None is returned, so the
    sender skips the drain instead of failing the service loop. The
    auth key never appears in any message.
    """

    kp = pyntara.metrics.open_runtime_vault(cfg)
    if kp is None:
        return None
    title = cfg.system_metrics_setup.google_script_key_entry_title
    entry = kp.find_entries(
        title=title, group=kp.root_group, recursive=False, first=True
    )
    if entry is None:
        _log(
            f"google script channel: entry {title!r} not found "
            "in the runtime vault",
            priority=3,
        )
        return None
    url = (entry.url or "").strip()
    key = (entry.password or "").strip()
    if not url or not key:
        _log(
            f"google script channel: entry {title!r} has an "
            "empty url or password",
            priority=3,
        )
        return None
    return url, key


def _ordered_entries(channel: Path, send_order: str) -> list[Path]:
    """Channel entries ordered by modification time according to send_order.

    The modification time of an entry is the commit time set by the
    commit command, so oldest_first sends the earliest committed entry
    first and newest_first the latest; ties are broken by name.
    """

    entries = sorted(
        (path for path in channel.iterdir() if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    if send_order == "newest_first":
        entries.reverse()
    return entries


def _entry_uploadable(entry: Path, limit: int) -> bool:
    """True when the entry is a regular non-empty file within the limit.

    The sender duplicates the ingest checks as a second line of defense
    (docs/spec/system-metrics.md, section Queue rules): a rejected entry
    is journaled and skipped, never uploaded.
    """

    try:
        entry_stat = entry.stat()
    except OSError as exc:
        _log(f"google script channel: cannot stat {entry}: {exc}", priority=3)
        return False
    if not stat.S_ISREG(entry_stat.st_mode):
        _log(
            f"google script channel: {entry.name} is not a regular file, "
            "skipping",
            priority=3,
        )
        return False
    if entry_stat.st_size == 0:
        _log(f"google script channel: {entry.name} is empty, skipping", priority=3)
        return False
    if entry_stat.st_size > limit:
        _log(
            f"google script channel: {entry.name} is {entry_stat.st_size} "
            f"bytes, larger than the limit of {limit} bytes, skipping",
            priority=3,
        )
        return False
    return True


def _send_entry(cfg: Config, entry: Path, url: str, key: str, sent: Path) -> None:
    """Upload one entry and move it to main_sent on success.

    The content is read and Base64-encoded; the original name is
    recovered by stripping the random suffix. curl runs with --location
    and an explicit --request POST, so the method and the body survive
    the redirect the web app endpoint answers with. The endpoint, the
    key and the name travel as separate argv entries and the command
    runs without a shell, so no metacharacter in them is interpreted.
    The process bound equals the configured curl timeout: curl's own
    --max-time is the effective limit, the process bound is a backstop
    that never fires in practice. On an OK response the entry moves to
    main_sent; every other outcome journals the failure and keeps the
    entry for the next cycle.
    """

    metrics = cfg.system_metrics_setup
    try:
        content = entry.read_bytes()
    except OSError as exc:
        _log(f"google script channel: cannot read {entry}: {exc}", priority=3)
        return
    data = base64.b64encode(content).decode("ascii")
    name = restore_original_name(entry.name, metrics.queue_file_suffix_length)
    timeout = metrics.google_script_timeout_seconds
    command = [
        "curl",
        "--location",
        "--request",
        "POST",
        "--max-time",
        str(timeout),
        "--silent",
        "--show-error",
        "--data-urlencode",
        f"filename={name}",
        "--data-urlencode",
        f"pass={key}",
        "--data-urlencode",
        f"data={data}",
        url,
    ]
    try:
        result = run_command(command, timeout=timeout, check=False, capture=True)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log(f"google script channel: sending {entry.name} failed: {exc}", priority=3)
        return
    if result.returncode != 0:
        _log(
            f"google script channel: sending {entry.name} failed: curl exited "
            f"{result.returncode}: {(result.stderr or '').strip()}",
            priority=3,
        )
        return
    output = (result.stdout or "").strip()
    if not output.startswith("OK "):
        _log(
            f"google script channel: sending {entry.name} failed: the web app "
            f"answered: {output}",
            priority=3,
        )
        return
    sent_path = sent / entry.name
    try:
        os.replace(entry, sent_path)
    except OSError as exc:
        _log(
            f"google script channel: sent {entry.name} but cannot move it to "
            f"{sent_path}: {exc}",
            priority=3,
        )
        return
    _log(f"google script channel: sent {entry.name}")
