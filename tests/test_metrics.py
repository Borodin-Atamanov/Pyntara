"""Unit tests for the System Metrics service loop.

The loop is exercised with a fake sender, so the retry mode, the pauses
and the reset behavior are asserted without real time or network. The
journal is disabled by conftest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support import make_config

from pyntara.metrics import _retry_delay, main


def test_main_loops_with_base_pause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # main reads the config path from the argument, loads the config and
    # runs the vault check, the dispatch and the Google send once per
    # configured interval; the loop is interrupted after the first sleep,
    # like a service stop.
    # main reads the config path from the argument, loads the config,
    # dispatches and sends once, then sleeps the backoff base; the loop is
    # interrupted after the first sleep, like a service stop.
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_backoff_base_seconds=2,
        system_metrics_backoff_multiplier=2,
        system_metrics_backoff_max_seconds=14400,
    )
    seen_paths: list[Path] = []
    dispatched: list[object] = []
    sent: list[object] = []
    pauses: list[int] = []

    def fake_load_config(path: Path) -> object:
        seen_paths.append(Path(path))
        return config

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        raise KeyboardInterrupt

    def fake_dispatch(cfg: object) -> None:
        dispatched.append(cfg)

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        sent.append(cfg)
        return 0, 0

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics", str(config_path)]
    )
    with pytest.raises(KeyboardInterrupt):
        main()
    assert seen_paths == [config_path]
    assert dispatched == [config]
    assert sent == [config]
    assert pauses == [2]


def test_retry_delay_grows_geometrically_and_caps() -> None:
    # With the default base 2 and multiplier 2 the first failure waits 2
    # seconds, every further failure doubles the pause, and the ceiling
    # of 14400 seconds cuts the growth: 2 x 2^13 = 16384 exceeds it.
    assert _retry_delay(1, 2, 2, 14400) == 2
    assert _retry_delay(2, 2, 2, 14400) == 4
    assert _retry_delay(3, 2, 2, 14400) == 8
    assert _retry_delay(13, 2, 2, 14400) == 8192
    assert _retry_delay(14, 2, 2, 14400) == 14400
    # No failures is a safe degenerate case: the base is returned.
    assert _retry_delay(0, 2, 2, 14400) == 2


def test_main_enters_retry_mode_and_grows_pauses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every cycle makes a send attempt and none succeeds: the loop enters
    # the retry mode after the first cycle and the pauses grow 2, 4, 8, 16.
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_backoff_base_seconds=2,
        system_metrics_backoff_multiplier=2,
        system_metrics_backoff_max_seconds=14400,
    )
    modes: list[bool] = []
    pauses: list[int] = []

    def fake_load_config(path: Path) -> object:
        del path
        return config

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 4:
            raise KeyboardInterrupt

    def fake_dispatch(cfg: object) -> None:
        del cfg

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        del cfg
        modes.append(single_random)
        return 1, 0

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr("sys.argv", ["pyntara.metrics", str(config_path)])
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
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_backoff_base_seconds=2,
        system_metrics_backoff_multiplier=2,
        system_metrics_backoff_max_seconds=14400,
    )
    results = [(1, 0), (1, 0), (1, 1), (1, 0)]
    pauses: list[int] = []

    def fake_load_config(path: Path) -> object:
        del path
        return config

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 4:
            raise KeyboardInterrupt

    def fake_dispatch(cfg: object) -> None:
        del cfg

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        del cfg
        return results.pop(0)

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr("sys.argv", ["pyntara.metrics", str(config_path)])
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
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_backoff_base_seconds=2,
        system_metrics_backoff_multiplier=2,
        system_metrics_backoff_max_seconds=14400,
    )
    results = [(0, 0), (1, 0)]
    pauses: list[int] = []

    def fake_load_config(path: Path) -> object:
        del path
        return config

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 2:
            raise KeyboardInterrupt

    def fake_dispatch(cfg: object) -> None:
        del cfg

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        del cfg
        return results.pop(0)

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr("sys.argv", ["pyntara.metrics", str(config_path)])
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [2, 2]


def test_main_caps_pause_at_maximum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The pause never exceeds backoff_max_seconds: with a ceiling of 16
    # the pauses grow 2, 4, 8, then stay at 16.
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_backoff_base_seconds=2,
        system_metrics_backoff_multiplier=2,
        system_metrics_backoff_max_seconds=16,
    )
    pauses: list[int] = []

    def fake_load_config(path: Path) -> object:
        del path
        return config

    def fake_sleep(seconds: float) -> None:
        pauses.append(int(seconds))
        if len(pauses) == 5:
            raise KeyboardInterrupt

    def fake_dispatch(cfg: object) -> None:
        del cfg

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        del cfg
        return 1, 0

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr("sys.argv", ["pyntara.metrics", str(config_path)])
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [2, 4, 8, 16, 16]
