"""Unit tests for the keyring_setup task.

The client never runs for real here: run_command is replaced with a recorded
fake and the session bus lookup with a fixed address, so no test reaches the
session bus of the machine it runs on (docs/guides/developer-guide.md). The
home directory of the desktop user is pointed at a temporary directory as well,
so no test reads or writes the wallet directory of the machine it runs on, and
the package install is replaced with an empty answer for the same reason.
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
CATALOG_NAME = "keyring_setup"
BUS_ADDRESS = "unix:path=/run/user/1000/bus"
PACKAGE_ANSWER = "install ok installed"
WALLET_FILES = (
    "kdewallet.kwl",
    "kdewallet.salt",
    "kdewallet_attributes.json",
)


class _Recorder:
    """The recorded run_command of one test."""

    def __init__(self, *, answer: str = "", client_failure: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.answer = answer
        self.client_failure = client_failure

    def __call__(self, command, **_kwargs) -> FakeProc:
        command_list = [str(part) for part in command]
        self.commands.append(command_list)
        text = " ".join(command_list)
        if "dpkg-query" in text:
            return FakeProc(0, PACKAGE_ANSWER)
        if self.client_failure:
            raise subprocess.CalledProcessError(
                1, command_list, output="", stderr="the client failed"
            )
        if "-c" in command_list:
            return FakeProc(0, self.answer)
        return FakeProc(0, "")

    @property
    def client_was_run(self) -> bool:
        """Whether the wallet client command was run at all."""

        return any("-c" in command for command in self.commands)


def _fixture_repo(tmp_path: Path) -> Path:
    """A clone of the test carrying the shipped client, as a run reads it."""

    directory = tmp_path / "task_data" / CATALOG_NAME
    directory.mkdir(parents=True, exist_ok=True)
    name = values.CLIENT_SCRIPT_FILE_NAME
    (directory / name).write_text(
        (CLIENT_DIRECTORY / name).read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def _context(tmp_path: Path, *, force: bool = False) -> Context:
    repo = _fixture_repo(tmp_path)
    return make_context(
        install_mode="desktop",
        repo_root=repo,
        task_data_root=repo,
        task_name=CATALOG_NAME,
        force_tasks=frozenset({CATALOG_NAME}) if force else frozenset(),
    )


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    answer: str = "",
    force: bool = False,
    session: bool = True,
    client_failure: bool = False,
) -> tuple[Context, _Recorder]:
    """A context and a recorded run_command, with the machine kept out."""

    monkeypatch.setattr(common_values, "DESKTOP_HOME_DIR", str(tmp_path))
    monkeypatch.setattr(
        task_module,
        "session_bus_address",
        lambda *_args, **_kwargs: BUS_ADDRESS if session else None,
    )
    monkeypatch.setattr(
        task_module,
        "install_missing_packages",
        lambda *_args, **_kwargs: ([], [], [], []),
    )
    recorder = _Recorder(answer=answer, client_failure=client_failure)
    monkeypatch.setattr(task_module, "run_command", recorder)
    return _context(tmp_path, force=force), recorder


def _wallet_directory(tmp_path: Path) -> Path:
    """The wallet directory of the temporary home of a test."""

    directory = tmp_path / values.WALLET_DIRECTORY_RELATIVE_PATH
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def test_read_value_names_are_declared() -> None:
    for name in values.READ_VALUE_NAMES:
        assert hasattr(values, name), name


def test_missing_values_report_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context, _ = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(task_module, "missing_value_names", lambda *_a, **_k: ["TRASH_PROGRAM"])

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is False
    assert result.message is not None
    assert "not declared" in result.message
    assert "TRASH_PROGRAM" in result.warnings[0]


def test_no_session_leaves_the_wallet_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context, recorder = _prepare(monkeypatch, tmp_path, session=False)

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is False
    assert result.message == "no live desktop session found"
    assert "may open a dialog" in result.warnings[0]
    assert recorder.client_was_run is False


def test_created_wallet_changes_the_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answer = f"{values.OUTCOME_KEY}={values.OUTCOME_CREATED}\n{values.DETAIL_KEY}=/w/kdewallet.kwl"
    context, _ = _prepare(monkeypatch, tmp_path, answer=answer)

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "created the KDE wallet without a password" in result.message
    assert result.warnings == ()


def test_existing_wallet_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answer = f"{values.OUTCOME_KEY}={values.OUTCOME_EXISTS}\n{values.DETAIL_KEY}=/w/kdewallet.kwl"
    context, _ = _prepare(monkeypatch, tmp_path, answer=answer)

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is False
    assert result.message is not None
    assert "already exists" in result.message
    assert result.warnings == ()


def test_force_moves_wallet_files_then_creates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answer = f"{values.OUTCOME_KEY}={values.OUTCOME_CREATED}\n{values.DETAIL_KEY}=/w"
    context, _ = _prepare(monkeypatch, tmp_path, answer=answer, force=True)
    directory = _wallet_directory(tmp_path)
    for name in (*WALLET_FILES, "notes.txt"):
        (directory / name).write_text("x", encoding="utf-8")
    calls: list[tuple[tuple[Path, ...], dict[str, object]]] = []

    def fake_move(paths, **kwargs):
        calls.append((tuple(paths), kwargs))
        return tuple(path.name for path in paths), ()

    monkeypatch.setattr(task_module, "move_paths_to_trash", fake_move)

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is True
    assert len(calls) == 1
    moved, arguments = calls[0]
    assert sorted(path.name for path in moved) == sorted(WALLET_FILES)
    assert arguments["username"] == common_values.DESKTOP_USERNAME
    assert arguments["home_dir"] == str(tmp_path)
    assert arguments["program"] == values.TRASH_PROGRAM


def test_force_without_wallet_files_only_creates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answer = f"{values.OUTCOME_KEY}={values.OUTCOME_CREATED}\n{values.DETAIL_KEY}=/w"
    context, _ = _prepare(monkeypatch, tmp_path, answer=answer, force=True)

    def unexpected_move(*_args, **_kwargs):
        raise AssertionError("nothing to move is not a move")

    monkeypatch.setattr(task_module, "move_paths_to_trash", unexpected_move)

    result = task_module.task(context)

    assert result.changed is True
    assert result.warnings == ()


def test_force_warns_when_the_wallet_file_comes_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answer = f"{values.OUTCOME_KEY}={values.OUTCOME_EXISTS}\n{values.DETAIL_KEY}=/w"
    context, _ = _prepare(monkeypatch, tmp_path, answer=answer, force=True)
    directory = _wallet_directory(tmp_path)
    (directory / WALLET_FILES[0]).write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        task_module, "move_paths_to_trash", lambda paths, **_kwargs: (
            tuple(path.name for path in paths),
            (),
        )
    )

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is False
    assert result.message == "the KDE wallet was not replaced"
    assert "still carries its password" in result.warnings[0]


def test_force_does_not_continue_when_the_move_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context, recorder = _prepare(monkeypatch, tmp_path, force=True)
    directory = _wallet_directory(tmp_path)
    (directory / WALLET_FILES[0]).write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        task_module,
        "move_paths_to_trash",
        lambda *_args, **_kwargs: ((), ("cannot move the wallet: the tool refused",)),
    )

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is False
    assert result.message == "the wallet files were not all replaced"
    assert "the tool refused" in result.warnings[0]
    assert recorder.client_was_run is False


def test_force_does_not_replace_without_a_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context, _ = _prepare(monkeypatch, tmp_path, force=True, session=False)

    def unexpected_move(*_args, **_kwargs):
        raise AssertionError("a wallet is not replaced without a session")

    monkeypatch.setattr(task_module, "move_paths_to_trash", unexpected_move)

    result = task_module.task(context)

    assert result.changed is False
    assert result.message == "no live desktop session found"


def test_client_failure_reports_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context, _ = _prepare(monkeypatch, tmp_path, client_failure=True)

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is False
    assert result.message == "the KDE wallet was not checked"
    assert "the client failed" in result.warnings[0]


def test_unknown_answer_reports_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context, _ = _prepare(monkeypatch, tmp_path, answer="outcome=whatever\ndetail=why")

    result = task_module.task(context)

    assert result.success is True
    assert result.changed is False
    assert result.message == "the KDE wallet was not checked"
    assert "whatever" in result.warnings[0]


def test_client_source_is_valid_after_substitution() -> None:
    source = task_module._client_source(
        CLIENT_DIRECTORY / values.CLIENT_SCRIPT_FILE_NAME
    )

    compile(source, values.CLIENT_SCRIPT_FILE_NAME, "exec")
    assert "$" not in source


def test_catalog_describes_the_wallet() -> None:
    records = {record.name: record.description for record in tasks_values.CATALOG}

    assert "wallet" in records[CATALOG_NAME].lower()


def test_the_old_client_is_gone() -> None:
    assert not (CLIENT_DIRECTORY / "configure_login_keyring.py").exists()
