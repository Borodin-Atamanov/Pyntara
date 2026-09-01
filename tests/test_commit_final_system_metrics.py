"""Unit tests for the commit_final_system_metrics task.

All external resources (subprocess, hostname, the system temp directory)
are mocked via monkeypatch; the tests only touch temporary fixtures
(docs/guides/developer-guide.md). The catalog checks load the real task
catalog so the ordering guarantee is verified against the actual config.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.config import load_config
from pyntara.context import Context
from pyntara.tasks import commit_final_system_metrics

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_TASKS = load_config(REPO_ROOT / "config").tasks
ALL_MODES = ("minimal", "server", "desktop")


def _ctx(tmp_path: Path) -> Context:
    """Context with the runtime vault rooted in the temporary directory."""

    vault = tmp_path / "var" / "lib" / "pyntara" / "secrets" / "pyntara.vault"
    return make_context(
        install_mode="server",
        force_tasks=frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=make_config(task_data_root=tmp_path, local_vault_path=vault),
    )


def _write_vault(tmp_path: Path, content: bytes = b"vault-bytes") -> Path:
    """Write the runtime vault fixture; return its path."""

    vault = tmp_path / "var" / "lib" / "pyntara" / "secrets" / "pyntara.vault"
    vault.parent.mkdir(parents=True)
    vault.write_bytes(content)
    return vault


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    hostname: str = "lusab-babad",
    fail: bool = False,
    timeout: bool = False,
) -> tuple[list[list[str]], Path, dict[str, object]]:
    """Replace hostname, the temp dir and subprocess; return the recorded data.

    The fake patches subprocess.run where run_command reaches it, so the
    real run_command wrapper is exercised; the commit command succeeds
    unless fail or timeout is requested. The temp directory is the
    fixture, so the temp copy name is observable; the copy content is
    captured from the file the fake receives.
    """

    monkeypatch.setattr(socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(
        commit_final_system_metrics.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )
    calls: list[list[str]] = []
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if timeout:
            raise subprocess.TimeoutExpired(command, 1)
        if command:
            path = Path(command[-1])
            if path.is_file():
                captured["path"] = path
                captured["content"] = path.read_bytes()
        if fail:
            return _FakeProc(1, "", "boom")
        return _FakeProc(0)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    temp_path = tmp_path / f"{hostname}.kdbx"
    return calls, temp_path, captured


def test_commits_vault_under_hostname_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The runtime vault is committed through the command under the name
    # <hostname>.kdbx, the content is preserved and the temp copy removed.
    _write_vault(tmp_path)
    calls, temp_path, captured = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "lusab-babad.kdbx" in result.message
    assert calls == [
        ["/usr/local/bin/commit_system_metrics", str(temp_path)]
    ]
    assert captured["path"] == temp_path
    assert captured["content"] == b"vault-bytes"
    assert not temp_path.exists()


def test_missing_vault_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No runtime vault means nothing to back up: an error is reported and
    # the commit command never runs (no silent failures).
    calls, _, _ = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is False
    assert result.error is not None
    assert "missing" in result.error
    assert calls == []


def test_empty_vault_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty runtime vault carries no data: an error is reported and the
    # commit command never runs.
    _write_vault(tmp_path, b"")
    calls, _, _ = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is False
    assert result.error is not None
    assert "empty" in result.error
    assert calls == []


def test_commit_failure_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A nonzero commit exit is an error carrying the command detail; the
    # temp copy is removed.
    _write_vault(tmp_path)
    calls, temp_path, _ = _install_fakes(monkeypatch, tmp_path, fail=True)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is False
    assert result.error is not None
    assert "commit failed" in result.error
    assert "boom" in result.error
    assert len(calls) == 1
    assert not temp_path.exists()


def test_commit_timeout_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A timed-out commit is an error; the temp copy is removed.
    _write_vault(tmp_path)
    calls, temp_path, _ = _install_fakes(monkeypatch, tmp_path, timeout=True)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is False
    assert result.error is not None
    assert "commit failed" in result.error
    assert len(calls) == 1
    assert not temp_path.exists()


def test_catalog_has_commit_final_last_in_every_mode() -> None:
    # The task must run after every other default task of a mode, so the
    # runtime vault exists and the queue machinery is deployed before the
    # backup is committed.
    for mode in ALL_MODES:
        defaults = task_catalog.default_tasks(mode, REAL_TASKS)
        assert defaults[-1] == "commit_final_system_metrics"


def test_catalog_depends_on_system_metrics_setup() -> None:
    # The task needs the commit command and the queue deployment of
    # system_metrics_setup and belongs to every install mode, mirroring
    # system_metrics_setup.
    task_def = next(
        task for task in REAL_TASKS if task.name == "commit_final_system_metrics"
    )
    assert task_def.depends == ("system_metrics_setup",)
    assert task_def.modes == ALL_MODES
