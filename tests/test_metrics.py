"""Unit tests for the System Metrics service module.

The periodic check runs against real KeePass databases in temporary
directories, so the open path is exercised for real (developer guide). The
journal is disabled by conftest; the log calls are captured through a fake
logger to assert the syslog priority and that the password never appears.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pykeepass import create_database
from support import make_config

from pyntara.config import Config
from pyntara.metrics import _retry_delay, check_runtime_vault, main

PASSWORD = "local-secret-password"
SUCCESS_MARK = "opens with the local password"


def _create_vault(path: Path, password: str) -> None:
    """Create a real KeePass vault with the given password."""

    create_database(str(path), password=password)


def _vault_config(tmp_path: Path) -> Config:
    """Config whose local vault paths live in the temporary directory."""

    return make_config(
        task_data_root=tmp_path,
        local_vault_path=tmp_path / "secrets" / "pyntara.vault",
        local_vault_pass_file_path=tmp_path / "etc" / "pass",
        system_metrics_check_interval_seconds=5,
    )


def _install_logger_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, int]]:
    """Replace the service logger with a capture; return the calls."""

    calls: list[tuple[str, int]] = []

    def fake_log(message: str, *, priority: int = 6) -> None:
        calls.append((message, priority))

    monkeypatch.setattr("pyntara.metrics._log", fake_log)
    return calls


def test_vault_opens_logs_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The vault exists and opens with the password from the password file:
    # the check succeeds and the success line is journaled at priority 7.
    vault = tmp_path / "secrets" / "pyntara.vault"
    vault.parent.mkdir(parents=True)
    _create_vault(vault, PASSWORD)
    pass_file = tmp_path / "etc" / "pass"
    pass_file.parent.mkdir(parents=True)
    pass_file.write_text(PASSWORD, encoding="utf-8")
    calls = _install_logger_capture(monkeypatch)
    assert check_runtime_vault(_vault_config(tmp_path)) is True
    assert any(SUCCESS_MARK in message for message, _ in calls)
    assert all(priority == 7 for message, priority in calls if SUCCESS_MARK in message)


def test_vault_absent_is_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No vault file: the check fails at priority 3.
    calls = _install_logger_capture(monkeypatch)
    assert check_runtime_vault(_vault_config(tmp_path)) is False
    assert any("absent" in message and priority == 3 for message, priority in calls)


def test_vault_empty_is_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty vault file is a failure: it cannot be a KeePass database.
    vault = tmp_path / "secrets" / "pyntara.vault"
    vault.parent.mkdir(parents=True)
    vault.write_bytes(b"")
    calls = _install_logger_capture(monkeypatch)
    assert check_runtime_vault(_vault_config(tmp_path)) is False
    assert any("empty" in message and priority == 3 for message, priority in calls)


def test_password_file_missing_is_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The vault exists but the password file does not: the check fails.
    vault = tmp_path / "secrets" / "pyntara.vault"
    vault.parent.mkdir(parents=True)
    _create_vault(vault, PASSWORD)
    calls = _install_logger_capture(monkeypatch)
    assert check_runtime_vault(_vault_config(tmp_path)) is False
    assert any("password file" in message and priority == 3 for message, priority in calls)


def test_wrong_password_is_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The vault opens with one password, the file carries another: failure.
    vault = tmp_path / "secrets" / "pyntara.vault"
    vault.parent.mkdir(parents=True)
    _create_vault(vault, "real-password")
    pass_file = tmp_path / "etc" / "pass"
    pass_file.parent.mkdir(parents=True)
    pass_file.write_text("wrong-password", encoding="utf-8")
    calls = _install_logger_capture(monkeypatch)
    assert check_runtime_vault(_vault_config(tmp_path)) is False
    assert any(
        "password does not match" in message and priority == 3
        for message, priority in calls
    )


def test_password_never_appears_in_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The password value must never be part of any journaled message, on
    # the success path or on the failure path (project rules: no secrets
    # in logs).
    vault = tmp_path / "secrets" / "pyntara.vault"
    vault.parent.mkdir(parents=True)
    _create_vault(vault, PASSWORD)
    pass_file = tmp_path / "etc" / "pass"
    pass_file.parent.mkdir(parents=True)
    pass_file.write_text(PASSWORD, encoding="utf-8")
    calls = _install_logger_capture(monkeypatch)
    check_runtime_vault(_vault_config(tmp_path))
    assert PASSWORD not in " ".join(message for message, _ in calls)


def test_main_loops_with_configured_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # main reads the config path from the argument, loads the config and
    # runs the vault check, the dispatch and the Google send once per
    # configured interval; the loop is interrupted after the first sleep,
    # like a service stop.
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_check_interval_seconds=5,
    )
    seen_paths: list[Path] = []
    checks: list[object] = []
    dispatched: list[object] = []
    sent: list[object] = []

    def fake_load_config(path: Path) -> object:
        seen_paths.append(Path(path))
        return config

    def fake_sleep(seconds: float) -> None:
        del seconds
        raise KeyboardInterrupt

    def fake_check(cfg: object) -> bool:
        checks.append(cfg)
        return True

    def fake_dispatch(cfg: object) -> None:
        dispatched.append(cfg)

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        sent.append(cfg)
        return 0, 0

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.check_runtime_vault", fake_check)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr(
        "sys.argv", ["pyntara.metrics", str(config_path)]
    )
    with pytest.raises(KeyboardInterrupt):
        main()
    assert seen_paths == [config_path]
    assert len(checks) == 1
    assert dispatched == [config]
    assert sent == [config]


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
        system_metrics_check_interval_seconds=300,
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

    def fake_check(cfg: object) -> bool:
        del cfg
        return True

    def fake_dispatch(cfg: object) -> None:
        del cfg

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        del cfg
        modes.append(single_random)
        return 1, 0

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.check_runtime_vault", fake_check)
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
    # A successful cycle returns the loop to the normal mode: the pause
    # after it is the configured interval, and the growth restarts from
    # the base on the next failure.
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_check_interval_seconds=300,
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

    def fake_check(cfg: object) -> bool:
        del cfg
        return True

    def fake_dispatch(cfg: object) -> None:
        del cfg

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        del cfg
        return results.pop(0)

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.check_runtime_vault", fake_check)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr("sys.argv", ["pyntara.metrics", str(config_path)])
    with pytest.raises(KeyboardInterrupt):
        main()
    # Two failures grow to 4, the success resets to the interval, the
    # next failure restarts from the base.
    assert pauses == [2, 4, 300, 2]


def test_main_cycle_without_attempts_stays_normal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A cycle without send attempts (an empty queue) does not grow the
    # pause: the loop keeps the configured interval.
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_check_interval_seconds=300,
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

    def fake_check(cfg: object) -> bool:
        del cfg
        return True

    def fake_dispatch(cfg: object) -> None:
        del cfg

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        del cfg
        return results.pop(0)

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.check_runtime_vault", fake_check)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr("sys.argv", ["pyntara.metrics", str(config_path)])
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [300, 2]


def test_main_caps_pause_at_maximum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The pause never exceeds backoff_max_seconds: with a ceiling of 16
    # the pauses grow 2, 4, 8, then stay at 16.
    config_path = tmp_path / "config.toml"
    config = make_config(
        task_data_root=tmp_path,
        system_metrics_check_interval_seconds=300,
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

    def fake_check(cfg: object) -> bool:
        del cfg
        return True

    def fake_dispatch(cfg: object) -> None:
        del cfg

    def fake_send(cfg: object, single_random: bool = False) -> tuple[int, int]:
        del cfg
        return 1, 0

    monkeypatch.setattr("pyntara.metrics.load_config", fake_load_config)
    monkeypatch.setattr("pyntara.metrics.check_runtime_vault", fake_check)
    monkeypatch.setattr("pyntara.metrics.time.sleep", fake_sleep)
    monkeypatch.setattr("pyntara.metrics_send.dispatch_entries", fake_dispatch)
    monkeypatch.setattr("pyntara.metrics_send.send_google_queue", fake_send)
    monkeypatch.setattr("sys.argv", ["pyntara.metrics", str(config_path)])
    with pytest.raises(KeyboardInterrupt):
        main()
    assert pauses == [2, 4, 8, 16, 16]
