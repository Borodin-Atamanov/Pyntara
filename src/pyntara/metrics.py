"""Long-running System Metrics service: dispatch and send loop.

The service runs continuously on the target machine; the systemd unit
system_metrics.service, deployed by the system_metrics_setup task, starts
it at boot and restarts it on failure. Every cycle dispatches the
committed entries from main_outbox into the channel queues and drains the
Google Drive channel into the web app; the sender opens the runtime
secret vault created by local_vault_setup on demand, and a failed open is
journaled through the shared pyntara.logger functions at error_priority.
The password itself is never logged. The service takes no argument and
reads no config file: every value comes from the pyntara values package,
which ships with the deployed code. The report collector adds an
encrypted telemetry PDF
next to the report, and the Telegram channel replaces the current
Google-only sending in a later stage (docs/spec/system-metrics.md).
"""

from __future__ import annotations

import time
from pathlib import Path

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

import pyntara.metrics_send
from pyntara.logger import configure_journal
from pyntara.logger import log_progress as _log
from pyntara.utils import backoff_delay
from pyntara.values import local_vault_setup as local_vault_values
from pyntara.values import system_metrics_setup as values


def _read_password(path: Path) -> str | None:
    """Runtime vault password from the password file, or None.

    The password file holds exactly the password: surrounding whitespace
    is trimmed and no newline is appended (docs/spec/secrets-model.md), so
    the read applies the same trimming. An unreadable or empty file means
    no password is available, and None is returned.
    """

    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def open_runtime_vault_with_reason() -> tuple[PyKeePass | None, str | None]:
    """Open the runtime vault; return the vault or the reason it is closed.

    A missing or empty vault, a missing or empty password file and a vault
    that does not open with the password are all failures, and each is
    returned as one readable line instead of being journaled here. The
    caller decides what to do with the line: a one-shot reader journals it
    at once, while the send loop reports it once per state change and not
    every cycle. The password never appears in the line. The vault and the
    password file are the values of the local_vault_setup section, so the
    deployed commands take no argument.
    """

    vault = local_vault_values.LOCAL_VAULT_PATH
    password_path = local_vault_values.PASS_FILE_PATH
    if not vault.is_file():
        return None, f"opening runtime vault {vault}: absent"
    try:
        if vault.stat().st_size == 0:
            return None, f"opening runtime vault {vault}: empty"
    except OSError:
        return None, f"opening runtime vault {vault}: cannot stat"
    password = _read_password(password_path)
    if password is None:
        return None, (
            f"opening runtime vault {vault}: password file "
            f"{password_path} missing or empty"
        )
    try:
        return PyKeePass(str(vault), password=password), None
    except CredentialsError:
        return None, f"opening runtime vault {vault}: password does not match"
    except Exception as exc:  # noqa: BLE001 - any open failure is a failed check
        return None, f"opening runtime vault {vault}: cannot open: {exc}"


def open_runtime_vault() -> PyKeePass | None:
    """Open the runtime vault and journal the reason it is closed.

    The shared opener of the one-shot readers: the collector, the vault
    backup and the channel senders that report a failure at once. The send
    loop of the long-running service uses open_runtime_vault_with_reason
    instead, so a missing support is journaled once per state change and
    not every cycle.
    """

    kp, reason = open_runtime_vault_with_reason()
    if reason is not None:
        _log(reason, priority=values.ERROR_PRIORITY)
    return kp


def _dispatch_and_send(single_random: bool) -> tuple[str | None, int, int]:
    """Run one dispatch and send cycle; return problem, attempts and sent.

    A local support that is missing (the queue directory, the runtime vault
    or the password that opens it) is returned as one line, so the loop can
    report it once and retry with the steady support pause; the counts are
    zero then. Any other failure of the cycle is returned the same way, so
    a broken cycle never kills the service.
    """

    try:
        problem = pyntara.metrics_send.dispatch_entries()
        if problem is not None:
            return problem, 0, 0
        outcome = pyntara.metrics_send.send_google_queue(single_random=single_random)
    except Exception as exc:  # noqa: BLE001 - a broken cycle must not kill the service
        return f"the System Metrics cycle failed: {exc}", 0, 0
    return outcome.problem, outcome.attempts, outcome.sent


def main() -> None:
    """Run the dispatch and send loop until the service stops.

    The systemd unit runs this module with no argument: every value the
    loop needs comes from the pyntara values package, which ships with the
    deployed code, so the service never reads a config file. Every cycle
    dispatches the committed entries into the channel queues and drains the
    Google Drive channel. Two kinds of failure drive two geometric pauses:
    a cycle that made send attempts and stored nothing grows the pause from
    the configured base by the multiplier until the network ceiling, while a
    cycle that could not run because a local support is missing (the queue
    directory, the runtime vault or its password) grows it until the shorter
    support ceiling, which a repaired machine notices within minutes instead
    of a whole network ceiling. A missing support is journaled once when it
    appears and once when it is over, never every cycle. Every other cycle
    resets the counter and waits the base
    (docs/spec/system-metrics.md, section Schedule and retry).
    """

    configure_journal(values.SERVICE_JOURNAL_IDENTIFIER)
    failed_cycles = 0
    last_problem: str | None = None
    while True:
        problem, attempts, sent = _dispatch_and_send(failed_cycles > 0)
        if problem != last_problem:
            if problem is None:
                if last_problem is not None:
                    _log("the missing System Metrics support is available again")
            else:
                _log(problem, priority=values.ERROR_PRIORITY)
            last_problem = problem
        if problem is not None:
            failed_cycles += 1
            pause = backoff_delay(
                failed_cycles,
                values.BACKOFF_BASE_SECONDS,
                values.BACKOFF_MULTIPLIER,
                values.SUPPORT_RETRY_MAX_SECONDS,
            )
        elif sent > 0 or attempts == 0:
            failed_cycles = 0
            pause = values.BACKOFF_BASE_SECONDS
        else:
            failed_cycles += 1
            pause = backoff_delay(
                failed_cycles,
                values.BACKOFF_BASE_SECONDS,
                values.BACKOFF_MULTIPLIER,
                values.BACKOFF_MAX_SECONDS,
            )
        time.sleep(pause)


if __name__ == "__main__":
    main()
