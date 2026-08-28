"""Task commit_final_system_metrics: commit the runtime vault into System Metrics.

The task runs last in the catalog. It takes the runtime secret vault
created by local_vault_setup (docs/spec/secrets-model.md, Runtime storage
on the target machine), copies it to a temporary file named from
vault_backup_file_name with the machine hostname and commits the copy
through the commit_system_metrics command, so the encrypted vault reaches
the operator's backup storage (docs/spec/system-metrics.md, section
Runtime vault backup). The task is a producer of the System Metrics queue:
the commit is the hand-off point, the deployed service drains the queue
and sends the file, the installer never waits for the upload. A missing or
empty runtime vault and a failed commit are reported as an error TaskResult
so the install log shows them (no silent failures). The temporary copy is
always removed.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command


def task(ctx: Context) -> TaskResult:
    """Commit the runtime vault under the configured backup name.

    The vault path comes from the local_vault_setup config, the backup
    file name and the commit command path from the system_metrics_setup
    config. The vault is copied to a temporary file named
    vault_backup_file_name with {hostname} replaced by the machine
    hostname, mode 0600, and committed through the commit command; the
    temporary copy is removed in all cases. A missing or empty vault and
    a failed commit return an error TaskResult.
    """

    vault_path = ctx.config.local_vault_setup.local_vault_path
    backup_name = ctx.config.system_metrics_setup.vault_backup_file_name.format(
        hostname=socket.gethostname()
    )
    command_path = ctx.config.system_metrics_setup.command_path
    timeout = ctx.config.engine.command_timeout_seconds

    if not vault_path.is_file():
        _log(f"runtime vault {vault_path} missing, cannot back it up")
        return TaskResult(
            success=False,
            error=f"runtime vault missing: {vault_path}",
        )
    try:
        if vault_path.stat().st_size == 0:
            _log(f"runtime vault {vault_path} empty, cannot back it up")
            return TaskResult(
                success=False,
                error=f"runtime vault empty: {vault_path}",
            )
    except OSError as exc:
        _log(f"runtime vault {vault_path} cannot be stat: {exc}")
        return TaskResult(
            success=False,
            error=f"runtime vault cannot be stat: {vault_path}: {exc}",
        )

    temp_path = Path(tempfile.gettempdir()) / backup_name
    _log(f"committing runtime vault {vault_path} as {backup_name}")
    try:
        shutil.copyfile(vault_path, temp_path)
        os.chmod(temp_path, 0o600)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        return TaskResult(
            success=False,
            error=f"cannot copy runtime vault to {temp_path}: {exc}",
        )
    try:
        result = run_command(
            [str(command_path), str(temp_path)],
            timeout=timeout,
            capture=True,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        temp_path.unlink(missing_ok=True)
        return TaskResult(
            success=False,
            error=f"commit failed: {exc}",
        )
    temp_path.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return TaskResult(
            success=False,
            error=f"commit failed: {detail or 'nonzero exit'}",
        )
    size = vault_path.stat().st_size
    _log(f"runtime vault committed as {backup_name} ({size} bytes)")
    return TaskResult(
        success=True,
        changed=True,
        message=f"runtime vault committed as {backup_name} ({size} bytes)",
    )
