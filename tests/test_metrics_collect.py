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
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config

from pyntara import metrics_collect
from pyntara.config import CollectorModuleConfig

IPV4 = CollectorModuleConfig(
    name="ipv4", command=("ip", "-4", "addr", "show", "scope", "global")
)
IPV6 = CollectorModuleConfig(
    name="ipv6", command=("ip", "-6", "addr", "show", "scope", "global")
)
HOSTNAME = CollectorModuleConfig(name="hostname", command=("hostname",))


def _config(tmp_path: Path, **kwargs: object):
    """Config with a safe command path and lock path inside tmp_path."""

    return make_config(
        task_data_root=tmp_path,
        system_metrics_command_path=tmp_path / "usr" / "local" / "bin" / "commit",
        system_metrics_collector_lock_file_path=(
            tmp_path / "run" / "collector.lock"
        ),
        **kwargs,
    )


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
    ok_module = CollectorModuleConfig(name="ok", command=("ok",))
    assert metrics_collect._run_module(ok_module, 15) == {
        "status": "ok",
        "output": "address 10.0.0.1\n",
    }
    empty_module = CollectorModuleConfig(name="empty", command=("empty",))
    assert metrics_collect._run_module(empty_module, 15) == {
        "status": "empty",
        "output": "",
    }
    bad_module = CollectorModuleConfig(name="bad", command=("bad",))
    assert metrics_collect._run_module(bad_module, 15) == {
        "status": "error",
        "output": "stdout\nstderr\n",
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
    module = CollectorModuleConfig(name="nope", command=("nope",))
    assert metrics_collect._run_module(module, 15) == {
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
    module = CollectorModuleConfig(name="slow", command=("slow",))
    result = metrics_collect._run_module(module, 15)
    assert result["status"] == "error"
    assert "timed out" in result["output"]


def test_percent_ready_counts_only_ok() -> None:
    # Two of four modules ok is 50 percent; an empty list is trivially
    # ready at 100 percent.
    entries = [
        {"status": "ok"},
        {"status": "ok"},
        {"status": "empty"},
        {"status": "error"},
    ]
    assert metrics_collect.percent_ready(entries) == 50
    assert metrics_collect.percent_ready([]) == 100


def test_collect_builds_report_body(monkeypatch: pytest.MonkeyPatch) -> None:
    # The report carries the generation time, the readiness percentage and
    # the full module results in the network and system sections.
    _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                0, "inet 10.0.0.1\n"
            ),
            ("ip", "-6", "addr", "show", "scope", "global"): _FakeProc(0, ""),
            ("hostname",): _FakeProc(0, "myhost\n"),
        },
    )
    cfg = _config(
        Path("/tmp"),
        system_metrics_collector_network_modules=(IPV4, IPV6),
        system_metrics_collector_system_modules=(HOSTNAME,),
    )
    report = metrics_collect.collect(cfg)
    assert report["ready_percent"] == 50
    assert report["network"] == [
        {"name": "ipv4", "status": "ok", "output": "inet 10.0.0.1\n"},
        {"name": "ipv6", "status": "empty", "output": ""},
    ]
    assert report["system"] == [
        {"name": "hostname", "status": "ok", "output": "myhost\n"}
    ]
    assert isinstance(report["generated_at"], str)
    assert len(report["generated_at"]) == 19


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
    cfg = _config(
        tmp_path,
        system_metrics_collector_threshold_percent=50,
        system_metrics_collector_network_modules=(IPV4, IPV6),
    )
    report = metrics_collect.collect_until_ready(cfg)
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
    cfg = _config(
        tmp_path,
        system_metrics_collector_threshold_percent=100,
        system_metrics_collector_retry_base_seconds=2,
        system_metrics_collector_retry_multiplier=2,
        system_metrics_collector_retry_max_seconds=2,
        system_metrics_collector_network_modules=(IPV4, IPV6),
    )
    report = metrics_collect.collect_until_ready(cfg)
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
    cfg = _config(
        tmp_path,
        system_metrics_collector_threshold_percent=0,
        system_metrics_collector_network_modules=(IPV4,),
    )
    report = metrics_collect.collect_until_ready(cfg)
    assert report["ready_percent"] == 0
    assert sleeps == []


def test_commit_report_writes_commits_and_removes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The report is written under its configured name into the temp
    # directory, committed through the command and the temporary file is
    # removed afterwards.
    calls = _fake_run(
        monkeypatch,
        {
            (str(tmp_path / "usr" / "local" / "bin" / "commit"), str(tmp_path / "network.json")): _FakeProc(0, "ok"),
        },
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    cfg = _config(tmp_path)
    report = {
        "generated_at": "2026-08-12-12-00-00",
        "ready_percent": 100,
        "network": [],
        "system": [],
    }
    assert metrics_collect._commit_report(cfg, report) is True
    assert calls == [[str(tmp_path / "usr" / "local" / "bin" / "commit"), str(tmp_path / "network.json")]]
    assert not (tmp_path / "network.json").exists()


def test_commit_report_failure_returns_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A nonzero commit exit is reported as a failed commit and the
    # temporary file is still removed.
    _fake_run(
        monkeypatch,
        {
            (str(tmp_path / "usr" / "local" / "bin" / "commit"), str(tmp_path / "network.json")): _FakeProc(
                1, "", "file already pending"
            ),
        },
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    cfg = _config(tmp_path)
    report = {
        "generated_at": "2026-08-12-12-00-00",
        "ready_percent": 100,
        "network": [],
        "system": [],
    }
    assert metrics_collect._commit_report(cfg, report) is False
    assert not (tmp_path / "network.json").exists()


def test_main_missing_config_argument_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_collect"]
    )
    with pytest.raises(SystemExit) as exc:
        metrics_collect.main()
    assert exc.value.code == 1


def test_main_collects_and_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The full flow: the config is loaded from the argument, the modules
    # are collected, the report is committed through the command and the
    # temporary file is removed.
    commit_cmd = str(tmp_path / "usr" / "local" / "bin" / "commit")
    calls = _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                0, "inet 10.0.0.1\n"
            ),
            (commit_cmd, str(tmp_path / "network.json")): _FakeProc(0, "ok"),
        },
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    cfg = _config(
        tmp_path,
        system_metrics_collector_network_modules=(IPV4,),
    )
    monkeypatch.setattr("pyntara.metrics_collect.load_config", lambda path: cfg)
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics_collect", str(tmp_path / "config.toml")]
    )
    metrics_collect.main()
    assert [commit_cmd, str(tmp_path / "network.json")] in calls
    assert not (tmp_path / "network.json").exists()


def test_main_exits_when_lock_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A second instance whose lock is already held exits quietly without
    # collecting or committing.
    lock_path = tmp_path / "run" / "collector.lock"
    lock_path.parent.mkdir(parents=True)
    handle = open(lock_path, "a+", encoding="utf-8")
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
        cfg = _config(
            tmp_path,
            system_metrics_collector_network_modules=(IPV4,),
        )
        monkeypatch.setattr("pyntara.metrics_collect.load_config", lambda path: cfg)
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
    commit_cmd = str(tmp_path / "usr" / "local" / "bin" / "commit")
    _fake_run(
        monkeypatch,
        {
            ("ip", "-4", "addr", "show", "scope", "global"): _FakeProc(
                0, "inet 10.0.0.1\n"
            ),
            (commit_cmd, str(tmp_path / "network.json")): _FakeProc(1, "", "spool missing"),
        },
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    cfg = _config(
        tmp_path,
        system_metrics_collector_network_modules=(IPV4,),
    )
    monkeypatch.setattr("pyntara.metrics_collect.load_config", lambda path: cfg)
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
    # configured name.
    monkeypatch.setattr(
        "pyntara.metrics_collect.tempfile.gettempdir", lambda: str(tmp_path)
    )
    cfg = _config(tmp_path)
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
    assert metrics_collect._commit_report(cfg, report) is True
    assert len(written_content) == 1
    assert json.loads(written_content[0]) == report
    assert not (tmp_path / "network.json").exists()
