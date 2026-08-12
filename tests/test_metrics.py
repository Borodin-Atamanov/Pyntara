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
from pyntara.metrics import check_runtime_vault, main

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

    def fake_send(cfg: object) -> None:
        sent.append(cfg)

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
