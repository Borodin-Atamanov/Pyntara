"""Long-running System Metrics service: periodic runtime vault check.

The service runs continuously on the target machine; the systemd unit
system_metrics.service, deployed by the system_metrics_setup task, starts
it at boot and restarts it on failure. Every check_interval_seconds it
verifies that the runtime secret vault created by local_vault_setup still
exists and opens with the password from the password file, and writes the
outcome to the system journal through the shared pyntara.logger functions
at syslog priority 7 (debug) on success and 3 (error) on failure. The
password itself is never logged. The service reads the single system
config /etc/pyntara/config.toml through the same loader as the installer,
so its parameters come from the same source of truth (architecture
contract section 3). The current check is a placeholder: the real
telemetry logic (encrypted PDF, queues, delivery channels) replaces it in
a later stage (docs/spec/telemetry.md).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

from pyntara.config import LocalVaultSetupConfig, load_config
from pyntara.logger import log_progress as _log

# Fixed deployment contract (architecture contract section 3): the single
# config of the target system, installed by the system_metrics_setup task.
SYSTEM_CONFIG_PATH = Path("/etc/pyntara/config.toml")


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


def check_runtime_vault(cfg: LocalVaultSetupConfig) -> bool:
    """Check that the runtime vault exists and opens with the local password.

    A missing or empty vault, a missing or empty password file and a vault
    that does not open with the password are all failures, journaled at
    syslog priority 3; a successful check is journaled at priority 7. The
    password never appears in any message.
    """

    vault = cfg.local_vault_path
    if not vault.is_file():
        _log(f"checking runtime vault {vault}: absent", priority=3)
        return False
    try:
        if vault.stat().st_size == 0:
            _log(f"checking runtime vault {vault}: empty", priority=3)
            return False
    except OSError:
        _log(f"checking runtime vault {vault}: cannot stat", priority=3)
        return False
    password = _read_password(cfg.pass_file_path)
    if password is None:
        _log(
            f"checking password file {cfg.pass_file_path}: missing or empty",
            priority=3,
        )
        return False
    try:
        PyKeePass(str(vault), password=password)
    except CredentialsError:
        _log(
            f"checking runtime vault {vault}: password does not match",
            priority=3,
        )
        return False
    except Exception as exc:  # noqa: BLE001 - any open failure is a failed check
        _log(f"checking runtime vault {vault}: cannot open: {exc}", priority=3)
        return False
    _log(
        f"checking runtime vault {vault}: opens with the local password",
        priority=7,
    )
    return True


def main() -> None:
    """Run the periodic check loop until the service is stopped.

    The config path comes from the first command line argument; the systemd
    unit passes the fixed system config path. The config is mandatory: a
    missing or invalid config exits with an error, matching the installer
    (a missing config stops the run).
    """

    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SYSTEM_CONFIG_PATH
    cfg = load_config(config_path)
    interval = cfg.system_metrics_setup.check_interval_seconds
    while True:
        check_runtime_vault(cfg.local_vault_setup)
        time.sleep(interval)


if __name__ == "__main__":
    main()
