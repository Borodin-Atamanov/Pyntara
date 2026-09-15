"""System Metrics report collector: gather module output into network-<hostname>.json.

The collector is a producer of the System Metrics queue: it runs the
configured console commands, keeps their full output, waits up to the
retry window for enough network modules to answer, writes the report as
network-<hostname>.json into the system temp directory and commits it
through the commit_system_metrics command. The hostname is the machine
hostname at collection time, obtained from socket.gethostname(). The
systemd timer system_metrics_collector.timer, deployed by the
system_metrics_setup task, starts the oneshot service
system_metrics_collector.service after boot and at the configured daily
time; the service reads the single system config from the command line
argument and does all waiting itself, so systemd never sleeps for it
(docs/spec/system-metrics.md, section Report collector). The report is a
JSON document: generated_at in the project datetime format,
ready_percent, and the network and system module results, each with its
status (ok, empty or error) and the full command output. A module that
printed a JSON document contributes it as structured data instead of a
string, so the addresses of the machine and the ssh commands that reach
them stay records with their fields. ready_percent counts modules and
never the records inside them, so the readiness of a machine does not
grow or shrink with the number of addresses it carries. A non-blocking
flock on the configured lock path keeps a second instance (a boot run
overrunning into the daily run) from committing at the same time; the
second instance exits.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from pyntara.config import (
    COLLECTOR_SECTION_KEYS,
    COLLECTOR_TABLE_KEYS,
    CollectorModuleConfig,
    Config,
    absent_config_keys,
    describe_absent_config_keys,
    load_config,
)
from pyntara.logger import configure_journal
from pyntara.logger import log_progress as _log
from pyntara.utils import (
    backoff_delay,
    run_command,
    substituted_command,
    trim_whitespace,
)


def _structured_document(output: str) -> object | None:
    """The JSON document of a module output, or None when it is text.

    A module that reports records (an address with its ssh command, the
    answers of the country services) prints a JSON array or object, and
    the report keeps that structure instead of a string the reader would
    have to parse again. A bare scalar stays text on purpose: an
    identifier that happens to be a number (a RustDesk ID) is not a
    document, and turning it into a number would change its meaning.
    """

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, (list, dict)):
        return parsed
    return None


def _run_module(
    module: CollectorModuleConfig,
    timeout_seconds: int,
    keys: dict[str, str],
    words: dict[str, str],
) -> dict[str, object]:
    """Run one configured command; return status and output.

    A command that exits 0 with non-empty stdout is ok; one that exits 0
    with empty stdout is empty; anything else (nonzero exit, a missing
    executable, a timeout) is error, with the captured output kept. The
    output of every branch is trimmed of leading and trailing whitespace
    before it enters the report, so the trailing newline of every console
    command never reaches the telemetry; a whitespace-only output is
    empty, because it carries no information. A module that printed a
    JSON document contributes it as structured data, so the report keeps
    the records and their fields instead of a string. The field names of
    the result and the words of its status are config values, so the
    reader of the report finds the shape in the config.
    """

    status_key = keys["status"]
    output_key = keys["output"]
    try:
        result = subprocess.run(
            list(module.command),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return {
            status_key: words["error"],
            output_key: f"command not found: {module.command[0]}",
        }
    except subprocess.TimeoutExpired:
        return {
            status_key: words["error"],
            output_key: f"timed out after {timeout_seconds} seconds",
        }
    output = trim_whitespace(result.stdout)
    if result.returncode != 0:
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        output = trim_whitespace(output)
        return {status_key: words["error"], output_key: output}
    if not output:
        return {status_key: words["empty"], output_key: ""}
    document = _structured_document(output)
    if document is not None:
        return {status_key: words["ok"], output_key: document}
    return {status_key: words["ok"], output_key: output}


def percent_ready(
    entries: list[dict[str, object]],
    percent_scale: int,
    status_key: str,
    ok_word: str,
) -> int:
    """Share of ok modules among the entries, in percent.

    An empty module list is trivially ready: there is nothing to wait
    for, so the share is the full scale the config carries. The share
    counts sources, never the
    records inside them: a module that reports thirty addresses is one
    answered source, exactly like a module that reports one, so the
    readiness of a machine never depends on how many addresses it
    carries. The name of the status field and the word that counts as an
    answer are config values.
    """

    if not entries:
        return percent_scale
    ready = sum(1 for entry in entries if entry[status_key] == ok_word)
    return int(ready * percent_scale / len(entries))


def collect(cfg: Config) -> dict[str, object]:
    """Run every configured module; return the report body.

    The network modules form the network section and drive
    ready_percent; the system modules form the system section and never
    affect the readiness. The full output of every module is kept as is;
    the report generation time carries the moment in the configured
    datetime format, and every name of the document and every word of a
    status comes from the config.
    """

    collector = cfg.system_metrics_setup.collector
    keys = collector.report_keys
    words = collector.report_status_words
    timeout_seconds = collector.command_timeout_seconds
    network = [
        {
            keys["name"]: module.name,
            **_run_module(module, timeout_seconds, keys, words),
        }
        for module in collector.network_modules
    ]
    system = [
        {
            keys["name"]: module.name,
            **_run_module(module, timeout_seconds, keys, words),
        }
        for module in collector.system_modules
    ]
    return {
        keys["generated_at"]: datetime.now()
        .astimezone()
        .strftime(cfg.engine.datetime_format),
        keys["ready_percent"]: percent_ready(
            network,
            cfg.engine.percent_scale,
            keys["status"],
            words["ok"],
        ),
        keys["network"]: network,
        keys["system"]: system,
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
        ready_percent = report[collector.report_keys["ready_percent"]]
        remaining = deadline - time.monotonic()
        # ready_percent is always an int from collect; the isinstance check
        # keeps mypy strict happy without changing the behavior.
        if (
            isinstance(ready_percent, int)
            and ready_percent >= collector.threshold_percent
        ) or remaining <= 0:
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

    The report file name is the configured report_file_name template
    with {hostname} replaced by the machine hostname at collection time.
    The report is written to the system temp directory with mode 0600
    and passed to the configured commit_system_metrics command, which
    publishes it into the spool under the same name; the temporary file
    is always removed. A failed write or a failed commit is journaled at
    the System Metrics error priority and reported as False.
    """

    collector = cfg.system_metrics_setup.collector
    error_priority = cfg.system_metrics_setup.error_priority
    commit_template = cfg.system_metrics_setup.commit_command
    if not commit_template:
        _log(
            "collecting report: the config names no commit_command",
            priority=error_priority,
        )
        return False
    hostname = socket.gethostname()
    report_name = collector.report_file_name.format(hostname=hostname)
    report_path = Path(tempfile.gettempdir()) / report_name
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        os.chmod(report_path, collector.report_file_mode)
    except OSError as exc:
        _log(
            f"collecting report: cannot write {report_path}: {exc}",
            priority=error_priority,
        )
        return False
    try:
        result = subprocess.run(
            substituted_command(
                commit_template,
                {
                    "command_path": str(cfg.system_metrics_setup.command_path),
                    "file": str(report_path),
                },
            ),
            capture_output=True,
            text=True,
            timeout=collector.command_timeout_seconds,
            check=False,
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


def _commit_telemetry_pdf(cfg: Config, report: dict[str, object]) -> None:
    """Build and commit the encrypted telemetry PDF, best effort.

    The PDF is an addition to the report: the caller commits network.json
    first, so any failure here drops only the PDF and never the report.
    The whole body is one guarded block, so a missing pikepdf, a broken
    render, a stale config or a failed commit all leave the collector
    running. Only the encrypted bytes reach the temporary file; the
    unencrypted PDF never touches the disk.
    """

    metrics = cfg.system_metrics_setup
    collector = metrics.collector
    commit_template = metrics.commit_command
    if not commit_template:
        _log(
            "collecting telemetry pdf: the config names no commit_command",
            priority=metrics.error_priority,
        )
        return
    try:
        from pyntara import telemetry_pdf

        hostname = socket.gethostname()
        pdf_bytes = telemetry_pdf.build(cfg, report, hostname)
        if pdf_bytes is None:
            return
        pdf_name = metrics.telemetry_pdf_report_file_name.format(
            hostname=hostname
        )
        pdf_path = Path(tempfile.gettempdir()) / pdf_name
        pdf_path.write_bytes(pdf_bytes)
        os.chmod(pdf_path, collector.report_file_mode)
        result = subprocess.run(
            substituted_command(
                commit_template,
                {
                    "command_path": str(metrics.command_path),
                    "file": str(pdf_path),
                },
            ),
            capture_output=True,
            text=True,
            timeout=collector.command_timeout_seconds,
            check=False,
        )
        pdf_path.unlink(missing_ok=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            _log(
                f"collecting telemetry pdf: commit failed: {detail}",
                priority=metrics.error_priority,
            )
    except Exception as exc:  # noqa: BLE001 - the PDF is best effort
        _log(
            f"collecting telemetry pdf: {exc}",
            priority=metrics.error_priority,
        )


def _acquire_lock(path: Path, error_priority: int) -> TextIO | None:
    """Take the non-blocking exclusive lock, or None when it is held.

    A missing parent directory is created. An unopenable lock file is an
    error at the configured error priority: the collector cannot
    guarantee single-instance semantics and exits loudly. A held lock is
    not an error: the running instance will commit its own report, so the
    second instance exits quietly.
    """

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The lock handle outlives this function, so the file cannot be
        # scoped to a with block; the caller closes it.
        handle = open(path, "a+", encoding="utf-8")  # noqa: SIM115
    except OSError as exc:
        _log(
            f"collecting report: cannot open lock {path}: {exc}",
            priority=error_priority,
        )
        raise SystemExit(1)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def trigger_collection(cfg: Config) -> bool:
    """Start the report collector once; report whether the call went through.

    A producer of a positive availability change wakes the collector
    through this one call, so the network report and the encrypted PDF are
    rebuilt as soon as a new address or a new port appears, and the
    running System Metrics service sends them. The collector is a oneshot
    unit and the configured command returns without waiting, so the caller
    never waits for the collection; the collector's own non-blocking lock
    skips a duplicate start while a collection is already running. A
    failed call is journaled and never raised, because the next scheduled
    collection still carries the current state. The command, the unit name
    and the priority are config values of the collector table.
    """

    collector = cfg.system_metrics_setup.collector
    command = substituted_command(
        collector.start_command,
        {"service_unit_name": collector.service_unit_name},
    )
    try:
        result = run_command(
            command,
            check=False,
            capture=True,
            timeout=cfg.engine.command_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(
            f"cannot trigger the metrics collector: {exc}",
            priority=cfg.system_metrics_setup.error_priority,
        )
        return False
    if result.returncode != 0:
        _log(
            f"cannot trigger the metrics collector: exited {result.returncode}",
            priority=cfg.system_metrics_setup.error_priority,
        )
        return False
    _log("metrics collector triggered for a fresh network report")
    return True


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
    metrics = cfg.system_metrics_setup
    collector = metrics.collector
    configure_journal(
        cfg.engine.with_journal_identifier(collector.journal_identifier)
    )
    absent = describe_absent_config_keys(
        (
            (
                "system_metrics_setup",
                absent_config_keys(metrics, COLLECTOR_SECTION_KEYS),
            ),
            (
                "system_metrics_setup.collector",
                absent_config_keys(collector, COLLECTOR_TABLE_KEYS),
            ),
        )
    )
    if absent:
        print(f"error: the collector cannot run: {absent}", file=sys.stderr)
        return
    try:
        lock = _acquire_lock(
            collector.lock_file_path, cfg.engine.error_priority
        )
        if lock is None:
            _log("another collector instance is running, exiting")
            return
        report = collect_until_ready(cfg)
        if not _commit_report(cfg, report):
            raise SystemExit(1)
        _commit_telemetry_pdf(cfg, report)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed run reports one line, never a traceback
        print(
            f"error: the collector failed: {exc}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
