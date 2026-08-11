"""Long-running System Metrics service: periodic runtime vault check.

The service runs continuously on the target machine; the systemd unit
system_metrics.service, deployed by the system_metrics_setup task, starts
it at boot and restarts it on failure. Every check_interval_seconds it
verifies that the runtime secret vault created by local_vault_setup still
exists and opens with the password from the password file, and writes the
outcome to the system journal through the shared pyntara.logger functions
at the configured syslog priorities (error_priority on failure,
success_priority on success). The password itself is never logged. The
service reads the single system config system_config_path through the same
loader as the installer, so its parameters come from the same source of
truth (architecture contract section 3). The current check is a
placeholder: the real System Metrics logic (encrypted PDF, queues, delivery
channels) replaces it in a later stage (docs/spec/system-metrics.md).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

from pyntara.config import Config, load_config
from pyntara.logger import log_progress as _log


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


def check_runtime_vault(cfg: Config) -> bool:
    """Check that the runtime vault exists and opens with the local password.

    A missing or empty vault, a missing or empty password file and a vault
    that does not open with the password are all failures, journaled at
    system_metrics_setup.error_priority; a successful check is journaled at
    system_metrics_setup.success_priority. The password never appears in
    any message.
    """

    vault = cfg.local_vault_setup.local_vault_path
    error_priority = cfg.system_metrics_setup.error_priority
    success_priority = cfg.system_metrics_setup.success_priority
    if not vault.is_file():
        _log(f"checking runtime vault {vault}: absent", priority=error_priority)
        return False
    try:
        if vault.stat().st_size == 0:
            _log(f"checking runtime vault {vault}: empty", priority=error_priority)
            return False
    except OSError:
        _log(f"checking runtime vault {vault}: cannot stat", priority=error_priority)
        return False
    password = _read_password(cfg.local_vault_setup.pass_file_path)
    if password is None:
        _log(
            f"checking password file {cfg.local_vault_setup.pass_file_path}: "
            "missing or empty",
            priority=error_priority,
        )
        return False
    try:
        PyKeePass(str(vault), password=password)
    except CredentialsError:
        _log(
            f"checking runtime vault {vault}: password does not match",
            priority=error_priority,
        )
        return False
    except Exception as exc:  # noqa: BLE001 - any open failure is a failed check
        _log(f"checking runtime vault {vault}: cannot open: {exc}", priority=error_priority)
        return False
    _log(
        f"checking runtime vault {vault}: opens with the local password",
        priority=success_priority,
    )
    return True


def main() -> None:
    """Run the periodic check loop until the service is stopped.

    The config path is the first command line argument; the systemd unit
    renders the configured system_config_path into the ExecStart line. A
    missing argument is an explicit error: without a config the service
    cannot know what to check.
    """

    if len(sys.argv) < 2:
        print("error: missing config path argument", file=sys.stderr)
        raise SystemExit(1)
    cfg = load_config(Path(sys.argv[1]))
    interval = cfg.system_metrics_setup.check_interval_seconds
    while True:
        check_runtime_vault(cfg)
        time.sleep(interval)


if __name__ == "__main__":
    main()
