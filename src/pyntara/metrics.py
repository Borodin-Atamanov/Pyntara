"""Long-running System Metrics service: dispatch and send loop.

The service runs continuously on the target machine; the systemd unit
system_metrics.service, deployed by the system_metrics_setup task, starts
it at boot and restarts it on failure. Every cycle dispatches the
committed entries from main_outbox into the channel queues and drains the
Google Drive channel into the web app; the sender opens the runtime
secret vault created by local_vault_setup on demand, and a failed open is
journaled through the shared pyntara.logger functions at error_priority.
The password itself is never logged. The service reads the single system
config system_config_path through the same loader as the installer, so
its parameters come from the same source of truth (architecture contract
section 3). The encrypted PDF generation and the Telegram channel replace
the current Google-only sending in a later stage
(docs/spec/system-metrics.md).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

import pyntara.metrics_send
from pyntara.config import Config, load_config
from pyntara.logger import log_progress as _log
from pyntara.utils import backoff_delay


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


def open_runtime_vault(cfg: Config) -> PyKeePass | None:
    """Open the runtime vault with the local password, or None.

    A missing or empty vault, a missing or empty password file and a
    vault that does not open with the password are all failures, each
    journaled at system_metrics_setup.error_priority. The password never
    appears in any message. The helper is the shared vault opener of the
    System Metrics service: the channel senders read the runtime vault
    through it.
    """

    vault = cfg.local_vault_setup.local_vault_path
    error_priority = cfg.system_metrics_setup.error_priority
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
    password = _read_password(cfg.local_vault_setup.pass_file_path)
    if password is None:
        _log(
            f"opening runtime vault {vault}: password file "
            f"{cfg.local_vault_setup.pass_file_path} missing or empty",
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

    The config path is the first command line argument; the systemd unit
    renders the configured system_config_path into the ExecStart line. A
    missing argument is an explicit error: without a config the service
    cannot know what to run. Every cycle dispatches the committed entries
    into the channel queues and drains the Google Drive channel; a
    failure of any step is journaled and the loop continues with the next
    cycle. The pause after a cycle is the retry backoff: a cycle with
    send attempts and no success grows the pause geometrically from the
    configured base by the multiplier until the ceiling, every other
    cycle resets the counter and waits the base
    (docs/spec/system-metrics.md, section Schedule and retry).
    """

    if len(sys.argv) < 2:
        print("error: missing config path argument", file=sys.stderr)
        raise SystemExit(1)
    cfg = load_config(Path(sys.argv[1]))
    metrics = cfg.system_metrics_setup
    failed_cycles = 0
    while True:
        pyntara.metrics_send.dispatch_entries(cfg)
        attempts, sent = pyntara.metrics_send.send_google_queue(
            cfg, single_random=failed_cycles > 0
        )
        if sent > 0 or attempts == 0:
            failed_cycles = 0
        else:
            failed_cycles += 1
        pause = backoff_delay(
            failed_cycles,
            metrics.backoff_base_seconds,
            metrics.backoff_multiplier,
            metrics.backoff_max_seconds,
        )
        time.sleep(pause)


if __name__ == "__main__":
    main()
