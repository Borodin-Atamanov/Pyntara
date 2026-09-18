"""Unit tests for the System Metrics report collector.

The collector is exercised with a fake subprocess runner, so the module
statuses, the readiness percentage, the retry loop and the commit are
asserted without real commands, time or network. The journal is disabled
by conftest.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_config

from pyntara import metrics_collect
from pyntara.config import Config
from pyntara.values import engine as engine_values
from pyntara.values import system_metrics_setup as values
from pyntara.values.system_metrics_setup import CollectorModule

IPV4 = CollectorModule(
    name="ipv4", command=("ip", "-4", "addr", "show", "scope", "global")
)
IPV6 = CollectorModule(
    name="ipv6", command=("ip", "-6", "addr", "show", "scope", "global")
)
IPV4_LINK = CollectorModule(
    name="ipv4_link", command=("ip", "-4", "addr", "show", "scope", "link")
)
IPV6_LINK = CollectorModule(
    name="ipv6_link", command=("ip", "-6", "addr", "show", "scope", "link")
)
HOSTNAME = CollectorModule(name="hostname", command=("hostname",))
# The vocabulary of the report document, as the declared values carry it:
# the tests never spell a field name themselves.
REPORT_KEYS = values.COLLECTOR.report_keys
REPORT_WORDS = values.COLLECTOR.report_status_words


def _config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs: Any
) -> Config:
    """Point the collector values at tmp_path and return a bare config.

    The command path and the lock path of a test run live inside tmp_path,
    so a test never touches the machine it runs on; every further keyword
    replaces one field of the collector record. Both module tables start
    empty, so a test names the modules it wants and nothing else runs.
    """

    kwargs.setdefault("network_modules", ())
    kwargs.setdefault("system_modules", ())
    monkeypatch.setattr(
        values, "COMMAND_PATH", tmp_path / "usr" / "local" / "bin" / "commit"
    )
    monkeypatch.setattr(
        values,
        "COLLECTOR",
        replace(
            values.COLLECTOR,
            lock_file_path=tmp_path / "run" / "collector.lock",
            **kwargs,
        ),
    )
    return make_config()


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    results: dict[tuple[str, ...], _FakeProc],
    *,
    raise_file_not_found: tuple[str, ...] | None = None,
    raise_timeout: tuple[str, ...] | None = None,
) -> list[list[str]]:
    """Install a fake subprocess runner; return the recorded calls.

    Commands are matched by their argv tuple; unmatched commands return
    success with empty output. raise_file_not_found and raise_timeout name
    argv tuples that must raise the corresponding subprocess error.
    """

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        key = tuple(command)
        if raise_file_not_found is not None and key == raise_file_not_found:
            raise FileNotFoundError("no such file")
        if raise_timeout is not None and key == raise_timeout:
            raise subprocess.TimeoutExpired(cmd=command, timeout=1)
        return results.get(key, _FakeProc(0, ""))

    monkeypatch.setattr("pyntara.metrics_collect.subprocess.run", fake_run)
    return calls


def _fake_hostname(monkeypatch: pytest.MonkeyPatch, name: str = "testhost") -> None:
    """Install a fake socket.gethostname returning a fixed name."""

    monkeypatch.setattr("pyntara.metrics_collect.socket.gethostname", lambda: name)


def _fake_time(
    monkeypatch: pytest.MonkeyPatch,
    *,
    monotonic_steps: list[float] | None = None,
) -> list[float]:
    """Install fake time helpers; return the recorded sleep calls.

    monotonic_steps, when given, is a queue of monotonic clock readings;
    without it the clock advances by one second per reading, so a retry
    window of two seconds exhausts after two collections.
    """

    sleeps: list[float] = []
    steps = list(monotonic_steps or [])
    now = [0.0]

    def fake_monotonic() -> float:
        if steps:
            return steps.pop(0)
        now[0] += 1.0
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("pyntara.metrics_collect.time.monotonic", fake_monotonic)
    monkeypatch.setattr("pyntara.metrics_collect.time.sleep", fake_sleep)
    return sleeps


def test_run_module_keeps_a_json_document_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An address module prints a JSON array of records; the report keeps
    # the records and their ssh fields instead of a string the reader
    # would have to parse again.
    records = [
        {
            "address": "10.10.0.1",
            "family": "ipv4",
            "interface": "enp87s0",
            "scope": "global",
            "ssh": "ssh -v -p 30222 10.10.0.1",
        }
    ]
    _fake_run(monkeypatch, {("addresses",): _FakeProc(0, json.dumps(records))})
    module = CollectorModule(name="addresses", command=("addresses",))
    assert metrics_collect._run_module(module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "ok",
        "output": records,
    }


def test_run_module_keeps_a_bare_scalar_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A RustDesk ID is a number written as text and must stay text: a
    # JSON number would lose that it is an identifier.
    _fake_run(monkeypatch, {("id",): _FakeProc(0, "123456789")})
    module = CollectorModule(name="id", command=("id",))
    assert metrics_collect._run_module(module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "ok",
        "output": "123456789",
    }


def test_ready_percent_counts_sources_not_records() -> None:
    # A source that reports thirty addresses weighs exactly as a source
    # that reports one, so the readiness of a machine never depends on
    # how many addresses it carries.
    entries: list[dict[str, object]] = [
        {
            "status": "ok",
            "output": [{"address": f"10.0.0.{index}"} for index in range(30)],
        },
        {"status": "empty", "output": ""},
    ]
    assert (
        metrics_collect.percent_ready(
            entries,
            engine_values.PERCENT_SCALE,
            REPORT_KEYS["status"],
            REPORT_WORDS["ok"],
        )
        == 50
    )


def test_run_module_classifies_ok_empty_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A zero exit with output is ok, a zero exit without output is empty
    # and a nonzero exit is error with the captured output kept.
    _fake_run(
        monkeypatch,
        {
            ("ok",): _FakeProc(0, "address 10.0.0.1\n"),
            ("empty",): _FakeProc(0, ""),
            ("bad",): _FakeProc(1, "stdout\n", "stderr\n"),
        },
    )
    ok_module = CollectorModule(name="ok", command=("ok",))
    assert metrics_collect._run_module(ok_module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "ok",
        "output": "address 10.0.0.1",
    }
    empty_module = CollectorModule(name="empty", command=("empty",))
    assert metrics_collect._run_module(empty_module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "empty",
        "output": "",
    }
    bad_module = CollectorModule(name="bad", command=("bad",))
    assert metrics_collect._run_module(bad_module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "error",
        "output": "stdout\nstderr",
    }


def test_run_module_reports_missing_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A missing executable is not a crash: the module reports error with
    # the failing command name.
    _fake_run(
        monkeypatch,
        {},
        raise_file_not_found=("nope",),
    )
    module = CollectorModule(name="nope", command=("nope",))
    assert metrics_collect._run_module(module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "error",
        "output": "command not found: nope",
    }


def test_run_module_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # A command that exceeds its timeout reports error with the timeout.
    _fake_run(
        monkeypatch,
        {},
        raise_timeout=("slow",),
    )
    module = CollectorModule(name="slow", command=("slow",))
    result = metrics_collect._run_module(module, 15, REPORT_KEYS, REPORT_WORDS)
    assert result["status"] == "error"
    output = result["output"]
    assert isinstance(output, str) and "timed out" in output


def test_run_module_trims_whitespace_only_output_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A module that printed only whitespace carries no information: its
    # output is trimmed to empty and the status is empty, not ok.
    _fake_run(
        monkeypatch,
        {("blank",): _FakeProc(0, "  \n\t\n  ")},
    )
    module = CollectorModule(name="blank", command=("blank",))
    assert metrics_collect._run_module(module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "empty",
        "output": "",
    }


def test_run_module_preserves_internal_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Multi-line output keeps its internal newlines while the leading and
    # trailing whitespace of the whole text is removed.
    _fake_run(
        monkeypatch,
        {("lines",): _FakeProc(0, "  \nfirst line\nsecond line\n\n  ")},
    )
    module = CollectorModule(name="lines", command=("lines",))
    assert metrics_collect._run_module(module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "ok",
        "output": "first line\nsecond line",
    }


def test_run_module_trims_joined_error_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The error branch trims the joined stdout plus stderr once: the raw
    # utility output keeps its internal shape, only the edges are cleaned.
    _fake_run(
        monkeypatch,
        {("bad",): _FakeProc(1, "line one\n\n", "\nline two\n")},
    )
    module = CollectorModule(name="bad", command=("bad",))
    assert metrics_collect._run_module(module, 15, REPORT_KEYS, REPORT_WORDS) == {
        "status": "error",
        "output": "line one\n\nline two",
    }


def test_percent_ready_counts_only_ok() -> None:
    # Two of four modules ok is 50 percent; an empty list is trivially
    # ready at 100 percent.
    entries: list[dict[str, object]] = [
        {"status": "ok"},
        {"status": "ok"},
        {"status": "empty"},
        {"status": "error"},
    ]
    assert (
        metrics_collect.percent_ready(
            entries,
            engine_values.PERCENT_SCALE,
            REPORT_KEYS["status"],
            REPORT_WORDS["ok"],
        )
        == 50
    )
    assert (
        metrics_collect.percent_ready(
            [],
            engine_values.PERCENT_SCALE,
            REPORT_KEYS["status"],
            REPORT_WORDS["ok"],
        )
        == 100
    )


def test_percent_ready_follows_the_configured_scale() -> None:
    # Another scale in the [engine] table is the scale the share is counted
    # with, so the factor is not a value of the module.
    entries: list[dict[str, object]] = [
        {"status": "ok"},
        {"status": "ok"},
        {"status": "empty"},
        {"status": "error"},
    ]
    assert (
        metrics_collect.percent_ready(
            entries,
            10,
            REPORT_KEYS["status"],
            REPORT_WORDS["ok"],
        )
        == 5
    )


def test_collect_builds_report_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The report carries the generation time, the readiness percentage and
    # the full module results in the network and system sections. The
    # global and link scope modules sit side by side, so the report
    # carries both the routed and the link-local addresses of the machine.
    _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                0, "inet 10.0.0.1\n"
            ),
            ("ip", "-6", "addr", "show", "scope", "global"): _FakeProc(0, ""),
            ("ip", "-4", "addr", "show", "scope", "link"): _FakeProc(
                0, "inet 169.254.0.42\n"
            ),
            ("ip", "-6", "addr", "show", "scope", "link"): _FakeProc(0, ""),
            ("hostname",): _FakeProc(0, "myhost\n"),
        },
    )
    _config(
        monkeypatch,
        tmp_path,
        network_modules=(IPV4, IPV6, IPV4_LINK, IPV6_LINK),
        system_modules=(HOSTNAME,),
    )
    report = metrics_collect.collect()
    assert report["ready_percent"] == 50
    assert report["network"] == [
        {"name": "ipv4", "status": "ok", "output": "inet 10.0.0.1"},
        {"name": "ipv6", "status": "empty", "output": ""},
        {"name": "ipv4_link", "status": "ok", "output": "inet 169.254.0.42"},
        {"name": "ipv6_link", "status": "empty", "output": ""},
    ]
    assert report["system"] == [
        {"name": "hostname", "status": "ok", "output": "myhost"}
    ]
    assert isinstance(report["generated_at"], str)
    assert len(report["generated_at"]) == 19


def test_the_report_vocabulary_comes_from_the_declared_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The field names of the report and the word that counts as an answer
    # are config values: another map and another word are the document the
    # collector writes and the module the readiness counts.
    keys = {
        "generated_at": "moment",
        "ready_percent": "share",
        "network": "sources",
        "system": "machine",
        "name": "module",
        "status": "state",
        "output": "text",
    }
    words = {"ok": "answered", "empty": "silent", "error": "failed"}
    _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                0, "inet 10.0.0.1\n"
            ),
            ("ip", "-6", "addr", "show", "scope", "global"): _FakeProc(0, ""),
        },
    )
    monkeypatch.setattr(engine_values, "DATETIME_FORMAT", "%H:%M")
    _config(
        monkeypatch,
        tmp_path,
        report_keys=keys,
        report_status_words=words,
        network_modules=(IPV4, IPV6),
    )
    report = metrics_collect.collect()
    assert report["share"] == 50
    assert report["sources"] == [
        {"module": "ipv4", "state": "answered", "text": "inet 10.0.0.1"},
        {"module": "ipv6", "state": "silent", "text": ""},
    ]
    assert report["machine"] == []
    generated_at = report["moment"]
    assert isinstance(generated_at, str)
    assert len(generated_at) == 5


def test_collect_until_ready_commits_immediately_at_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Half of the network modules answer: the threshold of 50 percent is
    # reached with the first collection and no retry sleep happens.
    _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                0, "inet 10.0.0.1\n"
            ),
            ("ip", "-6", "addr", "show", "scope", "global"): _FakeProc(0, ""),
        },
    )
    sleeps = _fake_time(monkeypatch)
    _config(
        monkeypatch,
        tmp_path,
        threshold_percent=50,
        network_modules=(IPV4, IPV6),
    )
    report = metrics_collect.collect_until_ready()
    assert report["ready_percent"] == 50
    assert sleeps == []


def test_collect_until_ready_waits_until_window_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No network module ever answers and the threshold is 100: the
    # collector retries with the geometric backoff until the retry window
    # is exhausted, then returns the report as is.
    _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(0, ""),
            ("ip", "-6", "addr", "show", "scope", "global"): _FakeProc(0, ""),
        },
    )
    sleeps = _fake_time(monkeypatch)
    _config(
        monkeypatch,
        tmp_path,
        threshold_percent=100,
        retry_base_seconds=2,
        retry_multiplier=2,
        retry_max_seconds=2,
        network_modules=(IPV4, IPV6),
    )
    report = metrics_collect.collect_until_ready()
    assert report["ready_percent"] == 0
    # The first retry waits the base, the second retry is cut by the
    # exhausted window.
    assert sleeps == [1]


def test_threshold_zero_collects_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A threshold of zero commits after the first collection even when no
    # network module answers.
    _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(0, ""),
        },
    )
    sleeps = _fake_time(monkeypatch)
    _config(
        monkeypatch,
        tmp_path,
        threshold_percent=0,
        network_modules=(IPV4,),
    )
    report = metrics_collect.collect_until_ready()
    assert report["ready_percent"] == 0
    assert sleeps == []


def test_commit_report_writes_commits_and_removes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The report is written under its configured name (with hostname
    # substituted) into the temp directory, committed through the command
    # and the temporary file is removed afterwards.
    _fake_hostname(monkeypatch, "testhost")
    report_name = "network-testhost.json"
    calls = _fake_run(
        monkeypatch,
        {
            (
                str(tmp_path / "usr" / "local" / "bin" / "commit"),
                str(tmp_path / report_name),
            ): _FakeProc(0, "ok"),
        },
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    _config(monkeypatch, tmp_path)
    report = {
        "generated_at": "2026-08-12-12-00-00",
        "ready_percent": 100,
        "network": [],
        "system": [],
    }
    assert metrics_collect._commit_report(report) is True
    assert calls == [
        [
            str(tmp_path / "usr" / "local" / "bin" / "commit"),
            str(tmp_path / report_name),
        ]
    ]
    assert not (tmp_path / report_name).exists()


def test_commit_report_failure_returns_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A nonzero commit exit is reported as a failed commit and the
    # temporary file is still removed.
    _fake_hostname(monkeypatch, "testhost")
    report_name = "network-testhost.json"
    _fake_run(
        monkeypatch,
        {
            (
                str(tmp_path / "usr" / "local" / "bin" / "commit"),
                str(tmp_path / report_name),
            ): _FakeProc(1, "", "file already pending"),
        },
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    _config(monkeypatch, tmp_path)
    report = {
        "generated_at": "2026-08-12-12-00-00",
        "ready_percent": 100,
        "network": [],
        "system": [],
    }
    assert metrics_collect._commit_report(report) is False
    assert not (tmp_path / report_name).exists()


def test_main_missing_config_argument_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", ["pyntara.metrics_collect"])
    with pytest.raises(SystemExit) as exc:
        metrics_collect.main()
    assert exc.value.code == 1


def test_main_reports_a_failed_run_in_one_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A failure while the collector works is also reported in one line, so
    # the journal never carries a traceback of the deployed service.
    def fail() -> dict[str, object]:
        raise RuntimeError("no space left on device")

    _config(monkeypatch, tmp_path)
    monkeypatch.setattr("pyntara.metrics_collect.collect_until_ready", fail)
    monkeypatch.setattr(
        "pyntara.metrics_collect.load_config", lambda path: make_config()
    )
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_collect", str(tmp_path / "config.toml")]
    )
    metrics_collect.main()
    captured = capsys.readouterr()
    assert "error: the collector failed: no space left on device" in captured.err
    assert "Traceback" not in captured.err


def test_main_journals_under_the_configured_collector_identifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The collector announces itself under the identifier of its own
    # subsection, never under the engine name, so a journal query separates
    # the collector from the service and from the run that deployed them.
    _config(monkeypatch, tmp_path)
    configured: list[str] = []
    monkeypatch.setattr(metrics_collect, "load_config", lambda path: make_config())
    monkeypatch.setattr(metrics_collect, "configure_journal", configured.append)
    monkeypatch.setattr(
        metrics_collect, "_acquire_lock", lambda path, error_priority: object()
    )
    monkeypatch.setattr(metrics_collect, "collect_until_ready", dict)
    monkeypatch.setattr(metrics_collect, "_commit_report", lambda report: True)
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_collect", str(tmp_path / "config.toml")]
    )
    metrics_collect.main()
    assert configured[-1] == values.COLLECTOR.journal_identifier


def test_main_collects_and_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The full flow: the config is loaded from the argument, the modules
    # are collected, the report is committed through the command and the
    # temporary file is removed. The report file name includes the
    # machine hostname.
    _fake_hostname(monkeypatch, "testhost")
    report_name = "network-testhost.json"
    commit_cmd = str(tmp_path / "usr" / "local" / "bin" / "commit")
    calls = _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                0, "inet 10.0.0.1\n"
            ),
            (commit_cmd, str(tmp_path / report_name)): _FakeProc(0, "ok"),
        },
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    _config(
        monkeypatch,
        tmp_path,
        network_modules=(IPV4,),
    )
    monkeypatch.setattr("pyntara.metrics_collect.load_config", lambda path: make_config())
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_collect", str(tmp_path / "config.toml")]
    )
    metrics_collect.main()
    assert [commit_cmd, str(tmp_path / report_name)] in calls
    assert not (tmp_path / report_name).exists()


def test_main_exits_when_lock_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A second instance whose lock is already held exits quietly without
    # collecting or committing.
    lock_path = tmp_path / "run" / "collector.lock"
    lock_path.parent.mkdir(parents=True)
    # The lock handle lives until the finally block below, so the file
    # cannot be scoped to a with block.
    handle = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        calls = _fake_run(
            monkeypatch,
            {
                ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                    0, "inet 10.0.0.1\n"
                ),
            },
        )
        _config(
            monkeypatch,
            tmp_path,
            network_modules=(IPV4,),
        )
        monkeypatch.setattr("pyntara.metrics_collect.load_config", lambda path: make_config())
        monkeypatch.setattr(
            "sys.argv", ["pyntara.metrics_collect", str(tmp_path / "config.toml")]
        )
        metrics_collect.main()
        assert calls == []
    finally:
        handle.close()


def test_main_commit_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed commit is an error exit, so the systemd restart policy
    # retries the collector.
    _fake_hostname(monkeypatch, "testhost")
    report_name = "network-testhost.json"
    commit_cmd = str(tmp_path / "usr" / "local" / "bin" / "commit")
    _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                0, "inet 10.0.0.1\n"
            ),
            (commit_cmd, str(tmp_path / report_name)): _FakeProc(
                1, "", "spool missing"
            ),
        },
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    _config(
        monkeypatch,
        tmp_path,
        network_modules=(IPV4,),
    )
    monkeypatch.setattr("pyntara.metrics_collect.load_config", lambda path: make_config())
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_collect", str(tmp_path / "config.toml")]
    )
    with pytest.raises(SystemExit) as exc:
        metrics_collect.main()
    assert exc.value.code == 1


def test_commit_report_json_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The committed file carries the report body as JSON under the
    # configured name with the hostname substituted.
    _fake_hostname(monkeypatch, "testhost")
    report_name = "network-testhost.json"
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    _config(monkeypatch, tmp_path)
    report = {
        "generated_at": "2026-08-12-12-00-00",
        "ready_percent": 50,
        "network": [{"name": "ipv4", "status": "ok", "output": "inet 1\n"}],
        "system": [],
    }
    calls: list[list[str]] = []
    written_content: list[str] = []

    def fake_commit(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        written_content.append(Path(command[1]).read_text(encoding="utf-8"))
        return _FakeProc(0, "ok")

    monkeypatch.setattr("pyntara.metrics_collect.subprocess.run", fake_commit)
    assert metrics_collect._commit_report(report) is True
    assert len(written_content) == 1
    assert json.loads(written_content[0]) == report
    assert not (tmp_path / report_name).exists()


class TestTriggerCollection:
    """Tests for the call that wakes the collector after a network change."""

    def test_starts_the_collector_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The collector is started without waiting for it, so a producer of
        # a positive availability change never blocks on the collection.
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            calls.append(list(command))
            return _FakeProc(0, "")

        monkeypatch.setattr(metrics_collect, "run_command", fake_run)
        _config(monkeypatch, tmp_path)
        assert metrics_collect.trigger_collection() is True
        assert calls == [
            [
                "systemctl",
                "start",
                "--no-block",
                values.COLLECTOR.service_unit_name,
            ]
        ]

    def test_a_failed_call_is_reported_and_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failed wake-up is journaled; the next scheduled collection still
        # carries the current state, so nothing else has to happen.
        monkeypatch.setattr(
            metrics_collect, "run_command", lambda *a, **k: _FakeProc(1, "")
        )
        assert metrics_collect.trigger_collection() is False
