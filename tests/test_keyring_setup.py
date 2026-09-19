"""Unit tests for the keyring_setup task.

The client never runs for real here: run_command is replaced with a recorded
fake and the session bus lookup with a fixed address, so no test reaches the
session bus of the machine it runs on (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc, make_context

from pyntara.context import Context
from pyntara.tasks import keyring_setup as task_module
from pyntara.values import common as common_values
from pyntara.values import keyring_setup as values
from pyntara.values import tasks as tasks_values

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_DIRECTORY = REPO_ROOT / "task_data" / "keyring_setup"
BUS_ADDRESS = "unix:path=/run/user/1000/bus"
CATALOG_NAME = "keyring_setup"


def _fixture_repo(tmp_path: Path) -> Path:
    """A clone of the test carrying the shipped client, as a run reads it."""

    directory = tmp_path / "task_data" / CATALOG_NAME
    directory.mkdir(parents=True, exist_ok=True)
    name = values.CLIENT_SCRIPT_FILE_NAME
    (directory / name).write_text(
        (CLIENT_DIRECTORY / name).read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def _context(tmp_path: Path) -> Context:
    repo = _fixture_repo(tmp_path)
    return make_context(
        install_mode="desktop",
        repo_root=repo,
        task_data_root=repo,
        task_name=CATALOG_NAME,
    )


def _live_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_module, "session_bus_address", lambda *_args, **_kwargs: BUS_ADDRESS
    )


def _no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_module, "session_bus_address", lambda *_args, **_kwargs: None
    )


def _packages_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_module,
        "install_missing_packages",
        lambda _ctx, _packages: ([], [], [], []),
    )


def _client_answers(monkeypatch: pytest.MonkeyPatch, stdout: str) -> list[list[str]]:
    """Replace the subprocess with a fake that answers and records the calls."""

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> FakeProc:
        calls.append(list(command))
        return FakeProc(0, stdout)

    monkeypatch.setattr(task_module, "run_command", fake_run)
    return calls


def _client_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **_kwargs: object) -> FakeProc:
        raise subprocess.CalledProcessError(1, command, output="", stderr="boom")

    monkeypatch.setattr(task_module, "run_command", fake_run)


def test_the_shipped_client_is_valid_python_after_substitution() -> None:
    source = task_module._client_source(
        CLIENT_DIRECTORY / values.CLIENT_SCRIPT_FILE_NAME
    )
    assert "$" not in source
    compile(source, values.CLIENT_SCRIPT_FILE_NAME, "exec")


def test_the_shipped_client_names_no_service_of_its_own() -> None:
    text = (CLIENT_DIRECTORY / values.CLIENT_SCRIPT_FILE_NAME).read_text(
        encoding="utf-8"
    )
    assert values.BUS_NAME not in text
    assert values.INTERNAL_INTERFACE_NAME not in text


def test_absent_values_are_reported_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        values,
        "READ_VALUE_NAMES",
        (*values.READ_VALUE_NAMES, "NOT_DECLARED_ANYWHERE"),
    )
    calls = _client_answers(monkeypatch, "")
    result = task_module.task(_context(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert "NOT_DECLARED_ANYWHERE" in result.warnings[0]
    assert calls == []


def test_no_live_session_is_a_warning_and_no_client_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _no_session(monkeypatch)
    calls = _client_answers(monkeypatch, "")
    result = task_module.task(_context(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert "session" in result.warnings[0]
    assert calls == []


def test_created_collection_is_reported_as_a_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _live_session(monkeypatch)
    _packages_installed(monkeypatch)
    login_path = "/org/freedesktop/secrets/collection/login"
    calls = _client_answers(monkeypatch, f"outcome=created\ndetail={login_path}\n")
    result = task_module.task(_context(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.warnings == ()
    assert result.message is not None
    assert login_path in result.message
    assert len(calls) == 1


def test_passwordless_collection_reports_no_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _live_session(monkeypatch)
    _packages_installed(monkeypatch)
    _client_answers(monkeypatch, "outcome=already_passwordless\ndetail=/x\n")
    result = task_module.task(_context(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings == ()


def test_protected_collection_is_left_alone_and_warned_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _live_session(monkeypatch)
    _packages_installed(monkeypatch)
    _client_answers(monkeypatch, "outcome=protected\ndetail=Denied: invalid\n")
    result = task_module.task(_context(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert "Denied: invalid" in result.warnings[0]


def test_a_failed_client_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _live_session(monkeypatch)
    _packages_installed(monkeypatch)
    _client_fails(monkeypatch)
    result = task_module.task(_context(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert "boom" in result.warnings[0]


def test_an_unknown_answer_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _live_session(monkeypatch)
    _packages_installed(monkeypatch)
    _client_answers(monkeypatch, "outcome=something_else\ndetail=?\n")
    result = task_module.task(_context(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert result.warnings
    assert "something_else" in result.warnings[0]


def test_the_wrapper_and_the_interpreter_come_from_the_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _live_session(monkeypatch)
    _packages_installed(monkeypatch)
    monkeypatch.setattr(values, "RUNUSER_COMMAND", ("sudo", "-u", "{username}", "--"))
    monkeypatch.setattr(values, "PYTHON_SCRIPT_COMMAND", ("/opt/python", "-c"))
    calls = _client_answers(monkeypatch, "outcome=already_passwordless\n")
    task_module.task(_context(tmp_path))
    command = calls[0]
    assert command[:4] == ["sudo", "-u", common_values.DESKTOP_USERNAME, "--"]
    assert command[4] == "/opt/python"
    assert any(values.BUS_NAME in part for part in command)


def test_the_answer_keeps_an_equals_sign_inside_the_detail() -> None:
    answer = task_module._parse_answer("outcome=protected\ndetail=a=b\n")
    assert answer == {"outcome": "protected", "detail": "a=b"}


def test_the_catalog_entry_depends_on_kde_settings_and_runs_on_desktops() -> None:
    entries = {spec.name: spec for spec in tasks_values.CATALOG}
    assert CATALOG_NAME in entries
    spec = entries[CATALOG_NAME]
    assert spec.depends == ("kde_settings",)
    assert spec.modes == ("desktop", "fast_desktop")
