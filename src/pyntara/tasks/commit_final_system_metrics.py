"""Task commit_final_system_metrics: commit the runtime vault and the
telemetry PDF into System Metrics.

The task runs last in the catalog. It commits two documents into the
System Metrics queue: the runtime secret vault created by
local_vault_setup as the configured vault backup, and an encrypted
telemetry PDF built from the latest network-<hostname>.json report
already in the queue. Both documents are producers of the queue: the
commit is the hand-off point, the deployed service drains the queue and
sends the files, the installer never waits for the upload. Each document
is best effort: a failure of one never stops the other, and every
failure is a warning of a completed task (docs/spec/system-metrics.md,
section Runtime vault backup). The temporary copies are always removed.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from pyntara.config import Config, SystemMetricsSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.metrics_commit import restore_original_name
from pyntara.models import TaskResult
from pyntara.utils import run_command, substituted_command


def _commit_runtime_vault(
    vault_path: Path,
    hostname: str,
    metrics: SystemMetricsSetupConfig,
    timeout: int,
) -> tuple[bool, list[str]]:
    """Commit the runtime vault; return (changed, warnings).

    A missing, empty or unreadable vault, a failed copy and a failed
    commit are all warnings; the function never raises. The temporary
    copy is removed in all cases.
    """

    backup_name = metrics.vault_backup_file_name.format(hostname=hostname)
    warnings: list[str] = []

    if not vault_path.is_file():
        _log(f"runtime vault {vault_path} missing, cannot back it up")
        warnings.append(f"runtime vault missing: {vault_path}")
        return False, warnings
    try:
        if vault_path.stat().st_size == 0:
            _log(f"runtime vault {vault_path} empty, cannot back it up")
            warnings.append(f"runtime vault empty: {vault_path}")
            return False, warnings
    except OSError as exc:
        _log(f"runtime vault {vault_path} cannot be stat: {exc}")
        warnings.append(f"runtime vault cannot be stat: {vault_path}: {exc}")
        return False, warnings

    temp_path = Path(tempfile.gettempdir()) / backup_name
    _log(f"committing runtime vault {vault_path} as {backup_name}")
    try:
        shutil.copyfile(vault_path, temp_path)
        os.chmod(temp_path, metrics.vault_backup_file_mode)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        warnings.append(f"cannot copy runtime vault to {temp_path}: {exc}")
        return False, warnings
    try:
        result = run_command(
            substituted_command(
                metrics.commit_command,
                {"command_path": str(metrics.command_path), "file": str(temp_path)},
            ),
            timeout=timeout,
            capture=True,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        temp_path.unlink(missing_ok=True)
        warnings.append(f"commit failed: {exc}")
        return False, warnings
    temp_path.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        warnings.append(f"commit failed: {detail or 'nonzero exit'}")
        return False, warnings
    size = vault_path.stat().st_size
    _log(f"runtime vault committed as {backup_name} ({size} bytes)")
    return True, []


def _latest_report(
    metrics: SystemMetricsSetupConfig, hostname: str
) -> dict[str, object] | None:
    """The latest network-<hostname>.json report in the queue, or None.

    The report the collector committed last sits in main_sent or, when
    the sender has not drained it yet, in main_outbox; the spool is not
    scanned because the ingest may not have moved the entry yet and the
    collector guarantees at least one committed report by the time this
    task runs (the initial collector started before it). The latest file
    by modification time whose original name matches the configured
    report_file_name is read and returned as a parsed JSON document;
    when no report is found or the read fails, None is returned.
    """

    report_name = metrics.collector.report_file_name.format(hostname=hostname)
    root = metrics.system_metrics_dir
    candidates: list[Path] = []
    for directory_name in (metrics.main_sent_dir, metrics.main_outbox_dir):
        directory = root / directory_name
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            original = restore_original_name(
                entry.name, metrics.queue_file_suffix_length
            )
            if original == report_name:
                candidates.append(entry)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    try:
        data: dict[str, object] = json.loads(candidates[0].read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _commit_telemetry_pdf_from_queue(
    cfg: Config,
    hostname: str,
    metrics: SystemMetricsSetupConfig,
    timeout: int,
) -> tuple[bool, list[str]]:
    """Build and commit the telemetry PDF from the latest report.

    The latest network-<hostname>.json report is read from the queue
    and passed to telemetry_pdf.build; the encrypted bytes are written
    to a temporary file named telemetry_pdf_report_file_name and
    committed through the configured commit command. A missing report,
    a build failure and a failed commit are all warnings; the function
    never raises. The temporary copy is removed in all cases.
    """

    warnings: list[str] = []
    report = _latest_report(metrics, hostname)
    if report is None:
        _log("no report found in the queue, skipping the telemetry PDF")
        return False, warnings
    try:
        from pyntara import telemetry_pdf

        pdf_bytes = telemetry_pdf.build(cfg, report, hostname)
    except Exception as exc:  # noqa: BLE001 - best effort, never stops the task
        warnings.append(f"telemetry PDF build failed: {exc}")
        return False, warnings
    if pdf_bytes is None:
        warnings.append("telemetry PDF build returned no bytes")
        return False, warnings
    pdf_name = metrics.telemetry_pdf_report_file_name.format(hostname=hostname)
    pdf_path = Path(tempfile.gettempdir()) / pdf_name
    try:
        pdf_path.write_bytes(pdf_bytes)
        os.chmod(pdf_path, metrics.collector.report_file_mode)
    except OSError as exc:
        pdf_path.unlink(missing_ok=True)
        warnings.append(f"cannot write telemetry PDF to {pdf_path}: {exc}")
        return False, warnings
    try:
        result = run_command(
            substituted_command(
                metrics.commit_command,
                {"command_path": str(metrics.command_path), "file": str(pdf_path)},
            ),
            timeout=timeout,
            capture=True,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        pdf_path.unlink(missing_ok=True)
        warnings.append(f"telemetry PDF commit failed: {exc}")
        return False, warnings
    pdf_path.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        warnings.append(
            f"telemetry PDF commit failed: {detail or 'nonzero exit'}"
        )
        return False, warnings
    _log(f"telemetry PDF committed as {pdf_name}")
    return True, []


def task(ctx: Context) -> TaskResult:
    """Commit the runtime vault and the telemetry PDF into System Metrics.

    The vault path comes from the local_vault_setup config, the file
    names and the commit command from the system_metrics_setup config.
    The vault is committed first; then the latest network-<hostname>.json
    report in the queue is read and an encrypted telemetry PDF is built
    from it and committed through the same command. Both documents are
    best effort: a failure of one never stops the other, and every
    failure is a warning of a completed task. An empty commit_command
    stops both, because nothing can be committed without it.
    """

    metrics = ctx.config.system_metrics_setup
    vault_path = ctx.config.local_vault_setup.local_vault_path
    hostname = socket.gethostname()
    timeout = ctx.config.engine.command_timeout_seconds

    if not metrics.commit_command:
        _log("system_metrics_setup names no commit_command, cannot commit")
        warning = "system_metrics_setup.commit_command is empty"
        return TaskResult(
            success=True,
            changed=False,
            message=warning,
            warnings=(warning,),
        )

    warnings: list[str] = []
    changed = False

    vault_changed, vault_warnings = _commit_runtime_vault(
        vault_path, hostname, metrics, timeout
    )
    if vault_changed:
        changed = True
    warnings.extend(vault_warnings)

    pdf_changed, pdf_warnings = _commit_telemetry_pdf_from_queue(
        ctx.config, hostname, metrics, timeout
    )
    if pdf_changed:
        changed = True
    warnings.extend(pdf_warnings)

    if changed:
        parts: list[str] = []
        if vault_changed:
            parts.append(
                "runtime vault committed as "
                + metrics.vault_backup_file_name.format(hostname=hostname)
            )
        if pdf_changed:
            parts.append(
                "telemetry PDF committed as "
                + metrics.telemetry_pdf_report_file_name.format(hostname=hostname)
            )
        message = "; ".join(parts)
    else:
        message = "nothing to commit" if not warnings else "; ".join(warnings)
    return TaskResult(
        success=True,
        changed=changed,
        message=message,
        warnings=tuple(warnings),
    )
