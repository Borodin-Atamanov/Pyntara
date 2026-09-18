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
    # A failure while the ingest works is reported in one line, so the
    # journal of the machine never carries a traceback.
    def fail() -> None:
        raise OSError("the spool is not readable")

    monkeypatch.setattr("pyntara.metrics_ingest.ingest_spool", fail)
    main()
    captured = capsys.readouterr()
    assert "error: the ingest failed: the spool is not readable" in captured.err
    assert "Traceback" not in captured.err
