"""Unit tests for the hostname task.

All external resources (subprocess, kernel hostname, randomness,
filesystem paths) are mocked via monkeypatch; the tests only touch
temporary fixtures (docs/guides/developer-guide.md). The randomness is
fixed so the generated name is deterministic.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara.tasks import hostname as task_module

# Four fixed bytes encode to the canonical proquint pair lusab-babad.
FIXED_BYTES = b"\x7f\x00\x00\x01"
FIXED_NAME = "lusab-babad"


def _ctx(tmp_path: Path, *, force: bool = False):
    """Context with the hostname file rooted in the temporary directory."""

    return make_context(
        install_mode="server",
        force_tasks=frozenset({"hostname"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(
            task_data_root=tmp_path,
            hostname_file=tmp_path / "etc" / "hostname",
            hostname_set_hostname_command=("hostnamectl", "set-hostname"),
        ),
    )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    kernel_name: str = "old-host",
    apply_ok: bool = True,
) -> list[list[str]]:
    """Replace randomness, kernel hostname and run_command; return calls.

    The randomness is fixed so the generated name is deterministic; the
    kernel hostname and the apply command outcome are configurable.
    """

    monkeypatch.setattr(task_module.secrets, "token_bytes", lambda n: FIXED_BYTES)
    monkeypatch.setattr(socket, "gethostname", lambda: kernel_name)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(command))
        if not apply_ok:
            raise subprocess.CalledProcessError(1, command)
        return _FakeProc(0, "")

    monkeypatch.setattr("pyntara.tasks.hostname.run_command", fake_run)
    return calls


def test_first_run_generates_writes_and_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing hostname file generates a fresh name, writes it and
    # applies it to the kernel.
    ctx = _ctx(tmp_path)
    calls = _install_fakes(monkeypatch, kernel_name="old-host")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    hostname_file = tmp_path / "etc" / "hostname"
    assert hostname_file.read_text(encoding="utf-8").strip() == FIXED_NAME
    assert calls == [["hostnamectl", "set-hostname", FIXED_NAME]]


def test_skip_when_already_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A valid proquint name in the file that the kernel already knows
    # skips the task without rewriting or reapplying.
    hostname_file = tmp_path / "etc" / "hostname"
    hostname_file.parent.mkdir(parents=True)
    hostname_file.write_text(f"{FIXED_NAME}\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    calls = _install_fakes(monkeypatch, kernel_name=FIXED_NAME)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is False
    assert calls == []


def test_foreign_name_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A name that is not a proquint (for example a stock hostname) is
    # replaced with a fresh generated name.
    hostname_file = tmp_path / "etc" / "hostname"
    hostname_file.parent.mkdir(parents=True)
    hostname_file.write_text("my-laptop\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    calls = _install_fakes(monkeypatch, kernel_name="my-laptop")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert hostname_file.read_text(encoding="utf-8").strip() == FIXED_NAME
    assert calls == [["hostnamectl", "set-hostname", FIXED_NAME]]


def test_valid_file_applied_without_regenerating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A valid proquint name in the file that the kernel does not yet know
    # is applied as is, without generating a fresh name.
    hostname_file = tmp_path / "etc" / "hostname"
    hostname_file.parent.mkdir(parents=True)
    hostname_file.write_text(f"{FIXED_NAME}\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    calls = _install_fakes(monkeypatch, kernel_name="old-host")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert hostname_file.read_text(encoding="utf-8").strip() == FIXED_NAME
    assert calls == [["hostnamectl", "set-hostname", FIXED_NAME]]


def test_force_regenerates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force mode generates a fresh name even when the current state is
    # already a valid proquint known to the kernel.
    hostname_file = tmp_path / "etc" / "hostname"
    hostname_file.parent.mkdir(parents=True)
    hostname_file.write_text(f"{FIXED_NAME}\n", encoding="utf-8")
    ctx = _ctx(tmp_path, force=True)
    calls = _install_fakes(monkeypatch, kernel_name=FIXED_NAME)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert hostname_file.read_text(encoding="utf-8").strip() == FIXED_NAME
    assert calls == [["hostnamectl", "set-hostname", FIXED_NAME]]


def test_write_failure_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hostname file that cannot be written fails the task with an error.
    # A regular file in place of the parent directory makes the mkdir
    # fail, so the write cannot proceed.
    etc = tmp_path / "etc"
    etc.write_text("not a directory", encoding="utf-8")
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, kernel_name="old-host")
    result = task_module.task(ctx)
    assert result.success is False
    assert "cannot write" in (result.error or "")


def test_apply_failure_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing apply command fails the task with an error.
    hostname_file = tmp_path / "etc" / "hostname"
    hostname_file.parent.mkdir(parents=True)
    hostname_file.write_text("", encoding="utf-8")
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, kernel_name="old-host", apply_ok=False)
    result = task_module.task(ctx)
    assert result.success is False
    assert "cannot apply" in (result.error or "")
