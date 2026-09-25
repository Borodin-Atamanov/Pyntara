"""Unit tests for the System Metrics spool ingest command.

The command is exercised with the ingest function mocked; the journal
identifier and the error path are the unit under test (developer guide).
Every path the ingest uses is a declared value, so the module takes no
argument at all and the unit starts it the same way on every machine.
"""

from __future__ import annotations

import pytest

from pyntara.metrics_ingest import main
from pyntara.values import system_metrics_setup as values


def test_main_journals_under_the_declared_service_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The ingest announces itself in the journal under the identifier of its
    # own section, never under the engine name.
    configured: list[str] = []
    monkeypatch.setattr("pyntara.metrics_ingest.configure_journal", configured.append)
    monkeypatch.setattr("pyntara.metrics_ingest.ingest_spool", lambda: None)
    main()
    assert configured[-1] == values.SERVICE_JOURNAL_IDENTIFIER


def test_main_ingests_the_spool_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # main runs one ingest pass; the queue and spool paths come from the
    # declared values of the module.
    ingested: list[bool] = []
    monkeypatch.setattr("pyntara.metrics_ingest.ingest_spool", lambda: ingested.append(True))
    main()
    assert ingested == [True]


def test_main_reports_a_failed_ingest_in_one_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A failure while the ingest works is reported in one line and exits
    # nonzero, so the systemd restart policy retries the spool; the
    # journal of the machine never carries a traceback.
    def fail() -> None:
        raise OSError("the spool is not readable")

    monkeypatch.setattr("pyntara.metrics_ingest.ingest_spool", fail)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "error: the ingest failed: the spool is not readable" in captured.err
    assert "Traceback" not in captured.err


def test_main_exits_nonzero_when_an_entry_is_left_behind(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A run that could not publish an entry exits nonzero, so the systemd
    # restart policy retries the whole spool instead of waiting for the
    # next file to appear beside the stuck one.
    monkeypatch.setattr("pyntara.metrics_ingest.ingest_spool", lambda: 2)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "left 2 spool entries unpublished" in captured.err
