"""Unit tests for the System Metrics service loop.

The loop is exercised with a fake sender, so the retry mode, the pauses
and the reset behavior are asserted without real time or network. The
journal is disabled by conftest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara.metrics import main
from pyntara.utils import backoff_delay
from pyntara.values import system_metrics_setup as values


def test_main_journals_under_the_declared_service_identifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The deployed service announces itself in the journal under the
    # identifier of its own section, never under the engine name: the entry
    # point hands the logger the engine table carrying that identifier.
    configured: list[str] = []

    def fake_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("pyntara.metrics.configure_journal", configured.append)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", lambda: None)
    monkeypatch.setattr(
        "pyntara.metrics_send.send_google_queue",
        lambda single_random=False: (0, 0),
    )
    with pytest.raises(KeyboardInterrupt):
        main()
    assert configured[-1] == values.SERVICE_JOURNAL_IDENTIFIER


def test_main_loops_with_base_pause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # main dispatches and sends once, then sleeps the backoff base; the
    # loop is interrupted after the first sleep, like a service stop.
    monkeypatch.setattr(values, "BACKOFF_BASE_SECONDS", 2)
    monkeypatch.setattr(values, "BACKOFF_MULTIPLIER", 2)
    monkeypatch.setattr(values, "BACKOFF_MAX_SECONDS", 14400)
    dispatched: list[object] = []
    sent: list[int] = []
    pauses: list[int] = []

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        raise KeyboardInterrupt

    def fake_dispatch() -> None:
        dispatched.append(True)

    def fake_send(single_random: bool = False) -> tuple[int, int]:
        sent.append(int(single_random))
        return 0, 0

    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    with pytest.raises(KeyboardInterrupt):
        main()
    assert dispatched == [True]
    assert sent == [0]
    assert pauses == [2]


def test_retry_delay_grows_geometrically_and_caps() -> None:
    # With the default base 2 and multiplier 2 the first failure waits 2
    # seconds, every further failure doubles the pause, and the ceiling
    # of 14400 seconds cuts the growth: 2 x 2^13 = 16384 exceeds it.
    assert backoff_delay(1, 2, 2, 14400) == 2
    assert backoff_delay(2, 2, 2, 14400) == 4
    assert backoff_delay(3, 2, 2, 14400) == 8
    assert backoff_delay(13, 2, 2, 14400) == 8192
    assert backoff_delay(14, 2, 2, 14400) == 14400
    # No failures is a safe degenerate case: the base is returned.
    assert backoff_delay(0, 2, 2, 14400) == 2


def test_main_enters_retry_mode_and_grows_pauses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every cycle makes a send attempt and none succeeds: the loop enters
    # the retry mode after the first cycle and the pauses grow 2, 4, 8, 16.
    monkeypatch.setattr(values, "BACKOFF_BASE_SECONDS", 2)
    monkeypatch.setattr(values, "BACKOFF_MULTIPLIER", 2)
    monkeypatch.setattr(values, "BACKOFF_MAX_SECONDS", 14400)
    modes: list[bool] = []
    pauses: list[int] = []

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 4:
            raise KeyboardInterrupt

    def fake_dispatch() -> None:
        pass

    def fake_send(single_random: bool = False) -> tuple[int, int]:
        modes.append(single_random)
        return 1, 0

    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [2, 4, 8, 16]
    # The first cycle is a full drain, the retry cycles are single-entry.
    assert modes == [False, True, True, True]


def test_main_resets_retry_mode_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A successful cycle resets the counter: the pause returns to the
    # base, and the growth restarts from the base on the next failure.
    monkeypatch.setattr(values, "BACKOFF_BASE_SECONDS", 2)
    monkeypatch.setattr(values, "BACKOFF_MULTIPLIER", 2)
    monkeypatch.setattr(values, "BACKOFF_MAX_SECONDS", 14400)
    results = [(1, 0), (1, 0), (1, 1), (1, 0)]
    pauses: list[int] = []

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 4:
            raise KeyboardInterrupt

    def fake_dispatch() -> None:
        pass

    def fake_send(single_random: bool = False) -> tuple[int, int]:
        return results.pop(0)

    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    with pytest.raises(KeyboardInterrupt):
        main()
    # Two failures grow to 4, the success resets the pause to the base,
    # the next failure restarts from the base.
    assert pauses == [2, 4, 2, 2]


def test_main_cycle_without_attempts_stays_normal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A cycle without send attempts (an empty queue) does not grow the
    # pause: the loop keeps the base.
    monkeypatch.setattr(values, "BACKOFF_BASE_SECONDS", 2)
    monkeypatch.setattr(values, "BACKOFF_MULTIPLIER", 2)
    monkeypatch.setattr(values, "BACKOFF_MAX_SECONDS", 14400)
    results = [(0, 0), (1, 0)]
    pauses: list[int] = []

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 2:
            raise KeyboardInterrupt

    def fake_dispatch() -> None:
        pass

    def fake_send(single_random: bool = False) -> tuple[int, int]:
        return results.pop(0)

    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [2, 2]


def test_main_caps_pause_at_maximum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The pause never exceeds backoff_max_seconds: with a ceiling of 16
    # the pauses grow 2, 4, 8, then stay at 16.
    monkeypatch.setattr(values, "BACKOFF_BASE_SECONDS", 2)
    monkeypatch.setattr(values, "BACKOFF_MULTIPLIER", 2)
    monkeypatch.setattr(values, "BACKOFF_MAX_SECONDS", 16)
    pauses: list[int] = []

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 5:
            raise KeyboardInterrupt

    def fake_dispatch() -> None:
        pass

    def fake_send(single_random: bool = False) -> tuple[int, int]:
        return 1, 0

    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [2, 4, 8, 16, 16]


