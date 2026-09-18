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

import sys
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


def open_runtime_vault() -> PyKeePass | None:
    """Open the runtime vault with the local password, or None.

    A missing or empty vault, a missing or empty password file and a
    vault that does not open with the password are all failures, each
    journaled at system_metrics_setup.error_priority. The password never
    appears in any message. The helper is the shared vault opener of the
    System Metrics service: the channel senders read the runtime vault
    through it. The vault and the password file are the values of the
    local_vault_setup section, so the deployed commands take no argument.
    """

    vault = local_vault_values.LOCAL_VAULT_PATH
    password_path = local_vault_values.PASS_FILE_PATH
    error_priority = values.ERROR_PRIORITY
    if not vault.is_file():
        _log(f"opening runtime vault {vault}: absent", priority=error_priority)
        return None
    try:
        if vault.stat().st_size == 0:
            _log(f"opening runtime vault {vault}: empty", priority=error_priority)
            return None
    except OSError:
        _log(f"opening runtime vault {vault}: cannot stat", priority=error_priority)
        return None
    password = _read_password(password_path)
    if password is None:
        _log(
            f"opening runtime vault {vault}: password file "
            f"{password_path} missing or empty",
            priority=error_priority,
        )
        return None
    try:
        return PyKeePass(str(vault), password=password)
    except CredentialsError:
        _log(
            f"opening runtime vault {vault}: password does not match",
            priority=error_priority,
        )
        return None
    except Exception as exc:  # noqa: BLE001 - any open failure is a failed check
        _log(
            f"opening runtime vault {vault}: cannot open: {exc}",
            priority=error_priority,
        )
        return None


def main() -> None:
    """Run the dispatch and send loop until the service stops.

    The systemd unit runs this module with no argument: every value the
    loop needs comes from the pyntara values package, which ships with
    the deployed code, so the service never reads a config file. Every
    cycle dispatches the committed entries into the channel queues and
    drains the Google Drive channel; a failure of any step is journaled
    and the loop continues with the next cycle. The pause after a cycle
    is the retry backoff: a cycle with send attempts and no success grows
    the pause geometrically from the configured base by the multiplier
    until the ceiling, every other cycle resets the counter and waits the
    base (docs/spec/system-metrics.md, section Schedule and retry).
    """

    configure_journal(values.SERVICE_JOURNAL_IDENTIFIER)
    failed_cycles = 0
    while True:
        try:
            pyntara.metrics_send.dispatch_entries()
            attempts, sent = pyntara.metrics_send.send_google_queue(
                single_random=failed_cycles > 0
            )
        except Exception as exc:  # noqa: BLE001 - a broken cycle must not kill the service
            print(f"error: the cycle failed: {exc}", file=sys.stderr)
            attempts, sent = 0, 0
        if sent > 0 or attempts == 0:
            failed_cycles = 0
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
