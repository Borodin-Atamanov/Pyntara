"""Unit tests for the System Metrics service loop.

The loop is exercised with a fake sender, so the retry mode, the pauses
and the reset behavior are asserted without real time or network. The
journal is disabled by conftest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara.metrics import main
from pyntara.metrics_send import ChannelOutcome
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
        lambda single_random=False: ChannelOutcome(0, 0, None),
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

    def fake_send(single_random: bool = False) -> ChannelOutcome:
        sent.append(int(single_random))
        return ChannelOutcome(0, 0, None)

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

    def fake_send(single_random: bool = False) -> ChannelOutcome:
        modes.append(single_random)
        return ChannelOutcome(1, 0, None)

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
    results = [
        ChannelOutcome(1, 0, None),
        ChannelOutcome(1, 0, None),
        ChannelOutcome(1, 1, None),
        ChannelOutcome(1, 0, None),
    ]
    pauses: list[int] = []

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 4:
            raise KeyboardInterrupt

    def fake_dispatch() -> None:
        pass

    def fake_send(single_random: bool = False) -> ChannelOutcome:
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
    results = [ChannelOutcome(0, 0, None), ChannelOutcome(1, 0, None)]
    pauses: list[int] = []

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 2:
            raise KeyboardInterrupt

    def fake_dispatch() -> None:
        pass

    def fake_send(single_random: bool = False) -> ChannelOutcome:
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

    def fake_send(single_random: bool = False) -> ChannelOutcome:
        return ChannelOutcome(1, 0, None)

    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [2, 4, 8, 16, 16]


def test_main_grows_the_pause_to_the_support_ceiling_and_reports_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A missing local support (a queue that cannot be prepared, a vault
    # that does not open) is reported once when it appears and grows the
    # pause to the shorter support ceiling, not the network ceiling, so a
    # repaired machine is noticed within minutes instead of hours.
    monkeypatch.setattr(values, "BACKOFF_BASE_SECONDS", 2)
    monkeypatch.setattr(values, "BACKOFF_MULTIPLIER", 2)
    monkeypatch.setattr(values, "SUPPORT_RETRY_MAX_SECONDS", 8)
    monkeypatch.setattr(values, "BACKOFF_MAX_SECONDS", 14400)
    pauses: list[int] = []
    reported: list[str] = []

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 4:
            raise KeyboardInterrupt

    def fake_dispatch() -> str | None:
        return "cannot prepare the System Metrics directory /var/lib/pyntara/metrics"

    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr(
        "pyntara.metrics._log",
        lambda message, **kwargs: reported.append(message),
    )
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [2, 4, 8, 8]
    assert reported == [
        "cannot prepare the System Metrics directory /var/lib/pyntara/metrics"
    ]


def test_main_reports_a_recovered_support_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The missing support is journaled once when it appears and once when
    # it is over, never every cycle.
    monkeypatch.setattr(values, "BACKOFF_BASE_SECONDS", 2)
    monkeypatch.setattr(values, "BACKOFF_MULTIPLIER", 2)
    monkeypatch.setattr(values, "SUPPORT_RETRY_MAX_SECONDS", 300)
    monkeypatch.setattr(values, "BACKOFF_MAX_SECONDS", 14400)
    problems: list[str | None] = [
        "the support is missing",
        "the support is missing",
        None,
    ]
    reported: list[str] = []

    def fake_dispatch() -> str | None:
        return problems.pop(0)

    def fake_sleep(seconds: float) -> None:
        # The third cycle found the support back; stop after it slept.
        if not problems:
            raise KeyboardInterrupt

    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr(
        "pyntara.metrics._log",
        lambda message, **kwargs: reported.append(message),
    )
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr(
        "pyntara.metrics_send.send_google_queue",
        lambda single_random=False: ChannelOutcome(0, 0, None),
    )
    with pytest.raises(KeyboardInterrupt):
        main()
    assert reported == [
        "the support is missing",
        "the missing System Metrics support is available again",
    ]


