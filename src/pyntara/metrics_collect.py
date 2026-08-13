"""System Metrics report collector: gather module output into network.json.

The collector is a producer of the System Metrics queue: it runs the
configured console commands, keeps their full output, waits up to the
retry window for enough network modules to answer, writes the report as
network.json into the system temp directory and commits it through the
commit_system_metrics command. The systemd timer
system_metrics_collector.timer, deployed by the system_metrics_setup
task, starts the oneshot service system_metrics_collector.service after
boot and at the configured daily time; the service reads the single
system config from the command line argument and does all waiting
itself, so systemd never sleeps for it (docs/spec/system-metrics.md,
section Report collector). The report is a JSON document: generated_at
in the project datetime format, ready_percent, and the network and
system module results, each with its status (ok, empty or error) and
the full command output. A non-blocking flock on the configured lock
path keeps a second instance (a boot run overrunning into the daily
run) from committing at the same time; the second instance exits.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from pyntara.config import Config, load_config
from pyntara.logger import log_progress as _log
from pyntara.utils import backoff_delay


def _run_module(module, timeout_seconds: int) -> dict[str, str]:
    """Run one configured command; return status and full output.

    A command that exits 0 with non-empty stdout is ok; one that exits 0
    with empty stdout is empty; anything else (nonzero exit, a missing
    executable, a timeout) is error, with the captured output kept.
    """

    try:
        result = subprocess.run(
            list(module.command),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return {
            "status": "error",
            "output": f"command not found: {module.command[0]}",
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "output": f"timed out after {timeout_seconds} seconds",
        }
    output = result.stdout
    if result.returncode != 0:
        if result.stderr:
            output = f"{output.rstrip()}\n{result.stderr}" if output else result.stderr
        return {"status": "error", "output": output}
    if not output:
        return {"status": "empty", "output": ""}
    return {"status": "ok", "output": output}


def percent_ready(entries: list[dict[str, object]]) -> int:
    """Share of ok modules among the entries, in percent.

    An empty module list is trivially ready: there is nothing to wait
    for, so the percentage is 100.
    """

    if not entries:
        return 100
    ready = sum(1 for entry in entries if entry["status"] == "ok")
    return int(ready * 100 / len(entries))


def collect(cfg: Config) -> dict[str, object]:
    """Run every configured module; return the report body.

    The network modules form the network section and drive
    ready_percent; the system modules form the system section and never
    affect the readiness. The full output of every module is kept as is;
    the report generation time uses the project datetime format
    YYYY-MM-DD-HH-MM-SS.
    """

    collector = cfg.system_metrics_setup.collector
    network = [
        {
            "name": module.name,
            **_run_module(module, collector.command_timeout_seconds),
        }
        for module in collector.network_modules
    ]
    system = [
        {
            "name": module.name,
            **_run_module(module, collector.command_timeout_seconds),
        }
        for module in collector.system_modules
    ]
    return {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S"),
        "ready_percent": percent_ready(network),
        "network": network,
        "system": system,
    }


def collect_until_ready(cfg: Config) -> dict[str, object]:
    """Collect until the threshold is reached or the window is exhausted.

    The first collection runs immediately. While the share of ok network
    modules stays below threshold_percent and the retry window is not
    exhausted, the collection is repeated after the geometric backoff:
    the first retry waits retry_base_seconds, every further retry
    multiplies the pause by retry_multiplier until retry_max_seconds,
    and a pause never exceeds the remaining window. When the window is
    exhausted, the last collection is returned as is, whatever the
    readiness (docs/spec/system-metrics.md, section Report collector).
    """

    collector = cfg.system_metrics_setup.collector
    deadline = time.monotonic() + collector.retry_max_seconds
    attempts = 0
    while True:
        attempts += 1
        report = collect(cfg)
        remaining = deadline - time.monotonic()
        if report["ready_percent"] >= collector.threshold_percent or remaining <= 0:
            return report
        pause = min(
            backoff_delay(
                attempts,
                collector.retry_base_seconds,
                collector.retry_multiplier,
                collector.retry_max_seconds,
            ),
            remaining,
        )
        time.sleep(pause)


def _commit_report(cfg: Config, report: dict[str, object]) -> bool:
    """Write the report under its configured name and commit it.

    The report is written to the system temp directory under
    report_file_name with mode 0600 and passed to the configured
    commit_system_metrics command, which publishes it into the spool
    under the same name; the temporary file is always removed. A failed
    write or a failed commit is journaled at the System Metrics error
    priority and reported as False.
    """

    collector = cfg.system_metrics_setup.collector
    error_priority = cfg.system_metrics_setup.error_priority
    report_path = Path(tempfile.gettempdir()) / collector.report_file_name
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        os.chmod(report_path, 0o600)
    except OSError as exc:
        _log(
            f"collecting report: cannot write {report_path}: {exc}",
            priority=error_priority,
        )
        return False
    try:
        result = subprocess.run(
            [str(cfg.system_metrics_setup.command_path), str(report_path)],
            capture_output=True,
            text=True,
            timeout=collector.command_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"collecting report: commit failed: {exc}", priority=error_priority)
        report_path.unlink(missing_ok=True)
        return False
    report_path.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        _log(f"collecting report: commit failed: {detail}", priority=error_priority)
        return False
    return True


def _acquire_lock(path: Path) -> TextIO | None:
    """Take the non-blocking exclusive lock, or None when it is held.

    A missing parent directory is created. An unopenable lock file is an
    error: the collector cannot guarantee single-instance semantics and
    exits loudly. A held lock is not an error: the running instance will
    commit its own report, so the second instance exits quietly.
    """

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+", encoding="utf-8")
    except OSError as exc:
        _log(f"collecting report: cannot open lock {path}: {exc}", priority=3)
        raise SystemExit(1)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> None:
    """Collect the report and commit it through the queue.

    The config path is the first command line argument; the collector
    service unit renders the configured system_config_path into the
    ExecStart line. A missing argument is an explicit error. A second
    running instance exits quietly under the flock. A failed commit is
    an error exit, so the systemd restart policy retries the collector.
    """

    if len(sys.argv) < 2:
        print("error: missing config path argument", file=sys.stderr)
        raise SystemExit(1)
    cfg = load_config(Path(sys.argv[1]))
    collector = cfg.system_metrics_setup.collector
    lock = _acquire_lock(collector.lock_file_path)
    if lock is None:
        _log("another collector instance is running, exiting")
        return
    report = collect_until_ready(cfg)
    if not _commit_report(cfg, report):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
