"""Unit tests for the deployed version helpers.

The helpers answer two questions for a task that deploys a unit: which
version the deployed interpreter reports, and what the rendered unit has
to carry. The subprocess and the file system are faked, so the tests only
touch temporary fixtures (docs/guides/developer-guide.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support import FakeProc

from pyntara import deployment

VERSION_COMMAND = (
    "{python}",
    "-c",
    "import pyntara; print(pyntara.__version__)",
)


def _venv_python(tmp_path: Path) -> Path:
    """A file that stands for the interpreter of a deployed venv."""

    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    return python


def test_the_reported_version_is_trimmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The command prints the version with a newline, so the reader trims
    # it through the shared helper instead of relying on the caller.
    monkeypatch.setattr(
        deployment, "run_command", lambda *args, **kwargs: FakeProc(0, "0.3.516\n")
    )
    assert (
        deployment.venv_package_version(
            VERSION_COMMAND, _venv_python(tmp_path), 5
        )
        == "0.3.516"
    )


def test_the_version_command_comes_from_the_caller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The argv of the check is a value of the caller: another command is
    # exactly what runs, with the interpreter filling its {python} slot.
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> FakeProc:
        calls.append(list(command))
        return FakeProc(0, "0.3.516\n")

    monkeypatch.setattr(deployment, "run_command", fake_run)
    python = _venv_python(tmp_path)
    command = ("myvenv-python", "--check", "{python}")
    assert deployment.venv_package_version(command, python, 5) == "0.3.516"
    assert calls == [["myvenv-python", "--check", str(python)]]


def test_a_missing_interpreter_reports_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Nothing is run when there is no interpreter to ask, so a missing
    # venv never turns into a failed subprocess call.
    called: list[tuple[object, ...]] = []

    def fake_run(*args: object, **kwargs: object) -> FakeProc:
        called.append(args)
        return FakeProc(0, "0.3.516\n")

    monkeypatch.setattr(deployment, "run_command", fake_run)
    assert (
        deployment.venv_package_version(
            VERSION_COMMAND, tmp_path / "absent" / "python", 5
        )
        is None
    )
    assert called == []


def test_a_failed_import_reports_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A venv whose import fails reports no version.
    monkeypatch.setattr(
        deployment, "run_command", lambda *args, **kwargs: FakeProc(1, "")
    )
    assert (
        deployment.venv_package_version(
            VERSION_COMMAND, _venv_python(tmp_path), 5
        )
        is None
    )


def test_a_call_that_does_not_answer_reports_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The call is bounded by the timeout of the caller; a hung venv must
    # not hold up the whole run.
    def fake_run(*args: object, **kwargs: object) -> FakeProc:
        raise subprocess.TimeoutExpired("python", 5)

    monkeypatch.setattr(deployment, "run_command", fake_run)
    assert (
        deployment.venv_package_version(
            VERSION_COMMAND, _venv_python(tmp_path), 5
        )
        is None
    )


def test_deployed_version_answers_with_the_interpreter_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The unit must carry the version of the code it will run, so the
    # deployed interpreter answers even when it differs from the version
    # of the running installer.
    monkeypatch.setattr(
        deployment, "run_command", lambda *args, **kwargs: FakeProc(0, "0.3.999\n")
    )
    version, warning = deployment.deployed_version(
        VERSION_COMMAND, _venv_python(tmp_path), 5, "0.3.516"
    )
    assert version == "0.3.999"
    assert warning is None


def test_deployed_version_reports_the_fallback_it_had_to_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An interpreter that cannot be asked leaves the fallback as the only
    # answer, and the warning names the gap instead of hiding it.
    monkeypatch.setattr(
        deployment, "run_command", lambda *args, **kwargs: FakeProc(1, "")
    )
    python = _venv_python(tmp_path)
    version, warning = deployment.deployed_version(
        VERSION_COMMAND, python, 5, "0.3.516"
    )
    assert version == "0.3.516"
    assert warning is not None
    assert str(python) in warning
    assert "0.3.516" in warning
