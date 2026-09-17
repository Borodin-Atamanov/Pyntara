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
from pyntara.values import hostname as hostname_values

# Four fixed bytes encode to the canonical proquint pair lusab-babad.
FIXED_BYTES = b"\x7f\x00\x00\x01"
FIXED_NAME = "lusab-babad"


def _ctx(tmp_path: Path, *, force: bool = False):
    """Context with the task data rooted in the temporary directory."""

    return make_context(
        task_name="hostname",
        install_mode="server",
        force_tasks=frozenset({"hostname"}) if force else frozenset(),
        task_data_root=tmp_path,
        config=make_config(task_data_root=tmp_path),
    )


def _use_hostname_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, random_bytes: int = 4
) -> None:
    """Point the task values at the temporary directory.

    The values are module constants, so a test patches the module for its
    own duration and monkeypatch restores the shipped values afterwards.
    """

    monkeypatch.setattr(
        hostname_values, "HOSTNAME_FILE", str(tmp_path / "etc" / "hostname")
    )
    monkeypatch.setattr(hostname_values, "RANDOM_BYTES", random_bytes)


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
    _use_hostname_values(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fakes(monkeypatch, kernel_name="old-host")
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    hostname_file = tmp_path / "etc" / "hostname"
    assert hostname_file.read_text(encoding="utf-8").strip() == FIXED_NAME
    assert calls == [["hostnamectl", "set-hostname", FIXED_NAME]]


def test_random_byte_count_comes_from_the_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another byte count in the values module is the count the randomness
    # is asked for, so a value the code ignored could not change the
    # length of the generated name.
    requested: list[int] = []

    def fake_token_bytes(count: int) -> bytes:
        requested.append(count)
        return FIXED_BYTES

    monkeypatch.setattr(task_module.secrets, "token_bytes", fake_token_bytes)
    monkeypatch.setattr(socket, "gethostname", lambda: "old-host")
    monkeypatch.setattr(
        "pyntara.tasks.hostname.run_command",
        lambda command, **kwargs: _FakeProc(0, ""),
    )
    _use_hostname_values(monkeypatch, tmp_path, random_bytes=8)
    result = task_module.task(_ctx(tmp_path))
    assert result.success is True
    assert requested == [8]


def test_skip_when_already_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A valid proquint name in the file that the kernel already knows
    # skips the task without rewriting or reapplying.
    hostname_file = tmp_path / "etc" / "hostname"
    hostname_file.parent.mkdir(parents=True)
    hostname_file.write_text(f"{FIXED_NAME}\n", encoding="utf-8")
    _use_hostname_values(monkeypatch, tmp_path)
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
    _use_hostname_values(monkeypatch, tmp_path)
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
    _use_hostname_values(monkeypatch, tmp_path)
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
    _use_hostname_values(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, force=True)
    calls = _install_fakes(monkeypatch, kernel_name=FIXED_NAME)
    result = task_module.task(ctx)
    assert result.success is True
    assert result.changed is True
    assert hostname_file.read_text(encoding="utf-8").strip() == FIXED_NAME
    assert calls == [["hostnamectl", "set-hostname", FIXED_NAME]]


def test_write_failure_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hostname file that cannot be written is reported as a warning and
    # the kernel name is still applied, because the two steps are
    # independent. A regular file in place of the parent directory makes
    # the mkdir fail, so the write cannot proceed.
    etc = tmp_path / "etc"
    etc.write_text("not a directory", encoding="utf-8")
    _use_hostname_values(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    calls = _install_fakes(monkeypatch, kernel_name="old-host")
    result = task_module.task(ctx)
    assert result.success is True
    assert any("cannot write" in warning for warning in result.warnings)
    assert len(calls) == 1
    assert calls[0][:2] == ["hostnamectl", "set-hostname"]
    assert calls[0][2]


def test_apply_failure_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing apply command is reported as a warning and the written
    # file keeps the generated name, so the next run applies it.
    hostname_file = tmp_path / "etc" / "hostname"
    hostname_file.parent.mkdir(parents=True)
    hostname_file.write_text("", encoding="utf-8")
    _use_hostname_values(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    _install_fakes(monkeypatch, kernel_name="old-host", apply_ok=False)
    result = task_module.task(ctx)
    assert result.success is True
    assert any("cannot apply" in warning for warning in result.warnings)
    assert hostname_file.read_text(encoding="utf-8").strip() != ""


def test_a_value_that_is_not_declared_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A name the values module does not declare costs the task and never
    # the run: the task names the missing value in plain words, changes
    # nothing and reports success with a warning, which is what the entry
    # point counts at the end of the run.
    _use_hostname_values(monkeypatch, tmp_path)
    monkeypatch.delattr(hostname_values, "RANDOM_BYTES")
    result = task_module.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is False
    assert any("RANDOM_BYTES" in warning for warning in result.warnings)
