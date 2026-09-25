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
import random
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pyntara.metrics
from pyntara.logger import log_progress as _log
from pyntara.metrics_commit import restore_original_name
from pyntara.utils import run_command, substituted_command
from pyntara.values import engine as engine_values
from pyntara.values import system_metrics_setup as values


@dataclass(frozen=True)
class ChannelOutcome:
    """Result of one drain of a channel queue.

    attempts is the number of send attempts of the cycle and sent the number
    of entries moved to the sent archive. problem carries one readable line
    when the drain could not run because a local support is missing (the
    channel directory or the runtime vault with its password), and None when
    the drain ran, whatever the per-entry results. The send loop separates a
    missing support, which a person or a later installer run repairs and
    which therefore gets the short support pause, from a refused upload,
    which the network ceiling already covers.
    """

    attempts: int
    sent: int
    problem: str | None


def dispatch_entries() -> str | None:
    """Link every main_outbox entry into every channel queue.

    The channel queues and the sent archive are ensured with the
    configured queue directory mode. For each main_outbox entry a hard
    link is created in every channel directory; only when all links
    succeeded is the name removed from main_outbox, so a channel that is
    enabled later receives only entries committed after its enablement.
    A queue directory that cannot be prepared and a failed link both keep
    the entry for the next cycle; the first such failure is returned as one
    readable line instead of being journaled here, so the send loop reports
    a broken queue once and retries with the support pause. None means the
    dispatch ran.
    """

    root = values.SYSTEM_METRICS_DIR
    outbox = root / values.MAIN_OUTBOX_DIR
    channels = [root / values.GOOGLE_SCRIPT_DIR]
    sent = root / values.MAIN_SENT_DIR
    for directory in (outbox, sent, *channels):
        try:
            directory.mkdir(
                mode=values.SYSTEM_METRICS_DIR_MODE, parents=True, exist_ok=True
            )
        except OSError as exc:
            return f"cannot prepare the System Metrics directory {directory}: {exc}"
    try:
        entries = sorted(outbox.iterdir())
    except OSError as exc:
        return f"cannot list the System Metrics outbox {outbox}: {exc}"
    problem: str | None = None
    for entry in entries:
        linked: list[Path] = []
        for channel in channels:
            target = channel / entry.name
            try:
                os.link(entry, target)
            except OSError as exc:
                if problem is None:
                    problem = (
                        f"dispatching {entry.name} into {channel}: failed: {exc}, "
                        "keeping it"
                    )
                for created in linked:
                    created.unlink(missing_ok=True)
                break
            linked.append(target)
        else:
            entry.unlink(missing_ok=True)
            _log(f"dispatched {entry.name} into the channel queues")
    return problem


def send_google_queue(single_random: bool = False) -> ChannelOutcome:
    """Drain the Google Drive channel queue into the web app.

    Every regular non-empty entry no larger than the configured limit is
    uploaded with curl: the original name (the random suffix stripped),
    the shared auth key from the runtime vault and the Base64 content.
    An OK response moves the entry to main_sent; an ERROR response, a
    curl failure or missing credentials keep every entry for the next
    cycle. Each entry is handled independently, so one failure never
    stops the drain. The outcome carries the number of send attempts and
    the number of entries moved to main_sent, where a skipped entry is not
    an attempt, and one readable line when a local support is missing. In
    the retry mode (single_random) exactly one randomly
    chosen uploadable entry is attempted, so one permanently rejected
    entry never blocks the drain of the rest
    (docs/spec/system-metrics.md, section Schedule and retry). The
    runtime vault is opened only when the queue holds at least one
    uploadable entry, so an idle queue never pays the vault open cost.
    """

    channel = values.SYSTEM_METRICS_DIR / values.GOOGLE_SCRIPT_DIR
    sent = values.SYSTEM_METRICS_DIR / values.MAIN_SENT_DIR
    try:
        sent.mkdir(mode=values.SYSTEM_METRICS_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        return ChannelOutcome(
            0, 0, f"google script channel: cannot prepare {sent}: {exc}"
        )
    if not channel.is_dir():
        return ChannelOutcome(0, 0, f"google script channel: queue {channel} missing")
    try:
        ordered = _ordered_entries(channel, values.SEND_ORDER)
    except OSError as exc:
        return ChannelOutcome(
            0, 0, f"google script channel: cannot list {channel}: {exc}"
        )
    entries = [
        entry
        for entry in ordered
        if _entry_uploadable(
            entry,
            values.MAX_QUEUE_FILE_SIZE_BYTES,
            engine_values.ERROR_PRIORITY,
        )
    ]
    if not entries:
        return ChannelOutcome(0, 0, None)
    credentials = _google_script_credentials()
    if isinstance(credentials, str):
        return ChannelOutcome(0, 0, credentials)
    url, key = credentials
    if single_random:
        chosen = random.choice(entries)
        return ChannelOutcome(
            1, 1 if _send_entry(chosen, url, key, sent) else 0, None
        )
    attempts = 0
    sent_count = 0
    for entry in entries:
        attempts += 1
        if _send_entry(entry, url, key, sent):
            sent_count += 1
    return ChannelOutcome(attempts, sent_count, None)


def _google_script_credentials() -> tuple[str, str] | str:
    """The Google web app url and auth key, or the reason they are absent.

    The entry whose title comes from system_metrics_setup
    .google_script_key_entry_title carries the web app endpoint in the
    url field and the shared auth key in the password field
    (docs/spec/secrets-model.md). A vault that does not open, a missing
    entry or an empty field is returned as one readable line instead of
    being journaled here, so the send loop reports a missing support once
    and not every cycle; the auth key never appears in the line. The
    successful answer is the (url, key) pair.
    """

    kp, reason = pyntara.metrics.open_runtime_vault_with_reason()
    if kp is None:
        return reason or "the runtime vault is unavailable"
    title = values.GOOGLE_SCRIPT_KEY_ENTRY_TITLE
    entry = kp.find_entries(
        title=title, group=kp.root_group, recursive=False, first=True
    )
    if entry is None:
        return f"google script channel: entry {title!r} not found in the runtime vault"
    url = (entry.url or "").strip()
    key = (entry.password or "").strip()
    if not url or not key:
        return f"google script channel: entry {title!r} has an empty url or password"
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
    if send_order == values.SEND_ORDER_NEWEST_FIRST:
        entries.reverse()
    return entries


def _entry_uploadable(entry: Path, limit: int, error_priority: int) -> bool:
    """True when the entry is a regular non-empty file within the limit.

    The sender duplicates the ingest checks as a second line of defense
    (docs/spec/system-metrics.md, section Queue rules): a rejected entry
    is journaled at the configured error priority and skipped, never
    uploaded.
    """

    try:
        entry_stat = entry.stat()
    except OSError as exc:
        _log(
            f"google script channel: cannot stat {entry}: {exc}",
            priority=error_priority,
        )
        return False
    if not stat.S_ISREG(entry_stat.st_mode):
        _log(
            f"google script channel: {entry.name} is not a regular file, skipping",
            priority=error_priority,
        )
        return False
    if entry_stat.st_size == 0:
        _log(
            f"google script channel: {entry.name} is empty, skipping",
            priority=error_priority,
        )
        return False
    if entry_stat.st_size > limit:
        _log(
            f"google script channel: {entry.name} is {entry_stat.st_size} "
            f"bytes, larger than the limit of {limit} bytes, skipping",
            priority=error_priority,
        )
        return False
    return True


def _answer_excerpt(answer: str, limit: int) -> str:
    """The answer of the web app as one bounded line for the journal.

    A refusing answer can be a whole HTML page of the provider, and the
    journal of the target machine must carry a readable line instead of
    that page. The first non-empty line is collapsed into a single line
    and cut to the configured length; the caller names the full length of
    the answer next to it, so nothing is hidden.
    """

    for line in answer.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed[:limit]
    return ""


def _send_entry(entry: Path, url: str, key: str, sent: Path) -> bool:
    """Upload one entry and move it to main_sent on success.

    The content is read and Base64-encoded; the original name is
    recovered by stripping the random suffix. The data arguments make
    the first request a POST; curl runs with --location and without a
    forced method, so on the 302 redirect the web app answers with it
    switches to GET by itself, the only method the final endpoint
    accepts (a forced POST there is answered with 405 and an HTML
    page). The endpoint, the key and the name travel as separate argv
    entries and the command runs without a shell, so no metacharacter
    in them is interpreted. The command line carries the shared auth
    key as an argv entry, so the call passes log_command=False: printing
    the command would leak the key into the log and the journal, and
    logging secret values is forbidden (project rules). The Base64
    content travels through stdin as --data-urlencode data@-: a payload
    argument would hit the
    kernel argv length limit (E2BIG) for files larger than about 96
    KiB. The process bound equals the configured curl timeout: curl's
    own --max-time is the effective limit, the process bound is a
    backstop that never fires in practice. On an OK response the entry
    moves to main_sent; every other outcome journals the failure and
    keeps the entry for the next cycle. Returns True when the entry
    moved to main_sent, False on every failure; the caller counts the
    call as one send attempt regardless of the outcome.
    """

    error_priority = engine_values.ERROR_PRIORITY
    try:
        content = entry.read_bytes()
    except OSError as exc:
        _log(
            f"google script channel: cannot read {entry}: {exc}",
            priority=error_priority,
        )
        return False
    data = base64.b64encode(content).decode("ascii")
    name = restore_original_name(entry.name, values.QUEUE_FILE_SUFFIX_LENGTH)
    timeout = values.GOOGLE_SCRIPT_TIMEOUT_SECONDS
    command = substituted_command(
        values.GOOGLE_SCRIPT_UPLOAD_COMMAND,
        {
            "timeout_seconds": str(timeout),
            "file_name": name,
            "key": key,
        },
    ) + [url]
    try:
        result = run_command(
            command,
            timeout=timeout,
            check=False,
            capture=True,
            input=data,
            log_command=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log(
            f"google script channel: sending {entry.name} failed: {exc}",
            priority=error_priority,
        )
        return False
    if result.returncode != 0:
        _log(
            f"google script channel: sending {entry.name} failed: curl exited "
            f"{result.returncode}: {(result.stderr or '').strip()}",
            priority=error_priority,
        )
        return False
    output = (result.stdout or "").strip()
    if not output.startswith(values.GOOGLE_SCRIPT_ANSWER_OK_PREFIX):
        excerpt = _answer_excerpt(
            output, values.GOOGLE_SCRIPT_ANSWER_EXCERPT_CHARS
        )
        _log(
            f"google script channel: sending {entry.name} failed: the web app "
            f"answered {len(output)} characters without the "
            f"{values.GOOGLE_SCRIPT_ANSWER_OK_PREFIX!r} prefix: {excerpt}",
            priority=error_priority,
        )
        return False
    sent_path = sent / entry.name
    try:
        os.replace(entry, sent_path)
    except OSError as exc:
        _log(
            f"google script channel: sent {entry.name} but cannot move it to "
            f"{sent_path}: {exc}",
            priority=error_priority,
        )
        return False
    _log(f"google script channel: sent {entry.name}")
    return True
