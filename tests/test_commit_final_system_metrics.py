"""Unit tests for the commit_final_system_metrics task.

All external resources (subprocess, hostname, the system temp directory,
the queue directories) are mocked via monkeypatch; the tests only touch
temporary fixtures (docs/guides/developer-guide.md). The catalog checks
load the real task catalog so the ordering guarantee is verified against
the actual config.
"""

from __future__ import annotations

import json
import socket
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from support import FakeProc as _FakeProc
from support import make_config, make_context

from pyntara import task_catalog
from pyntara.context import Context
from pyntara.tasks import commit_final_system_metrics
from pyntara.values import tasks as tasks_values

REAL_TASKS = tasks_values.CATALOG
ALL_MODES = ("minimal", "server", "desktop")


def _ctx(tmp_path: Path) -> Context:
    """Context with the runtime vault and the queue rooted in tmp_path."""

    vault = tmp_path / "var" / "lib" / "pyntara" / "secrets" / "pyntara.vault"
    metrics_dir = tmp_path / "var" / "lib" / "pyntara" / "metrics"
    return make_context(
        install_mode="server",
        force_tasks=frozenset(),
        task_data_root=tmp_path,
        skip_apt_update=True,
        config=make_config(
            local_vault_path=vault,
            system_metrics_dir=metrics_dir,
        ),
    )


def _write_vault(tmp_path: Path, content: bytes = b"vault-bytes") -> Path:
    """Write the runtime vault fixture; return its path."""

    vault = tmp_path / "var" / "lib" / "pyntara" / "secrets" / "pyntara.vault"
    vault.parent.mkdir(parents=True)
    vault.write_bytes(content)
    return vault


def _write_queue_report(
    tmp_path: Path,
    hostname: str,
    report: dict[str, object],
    *,
    directory: str = "main_sent",
) -> Path:
    """Write a fake report into the queue directory; return its path.

    The report file name is network-{hostname}.json with a 12-character
    random suffix, matching the config defaults of the test context, so
    _latest_report finds it by its original name.
    """

    metrics_dir = tmp_path / "var" / "lib" / "pyntara" / "metrics"
    queue_dir = metrics_dir / directory
    queue_dir.mkdir(parents=True, exist_ok=True)
    report_name = f"network-{hostname}.json.abcdefghijkl"
    report_path = queue_dir / report_name
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


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
    # The runtime vault is committed under the name <hostname>.kdbx, the
    # content is preserved and the temp copy removed. No report is in the
    # queue, so the PDF is skipped without a warning.
    _write_vault(tmp_path)
    calls, temp_path, captured = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "lusab-babad.kdbx" in result.message
    assert calls == [["/usr/local/bin/commit_system_metrics", str(temp_path)]]
    assert captured["path"] == temp_path
    assert captured["content"] == b"vault-bytes"
    assert not temp_path.exists()


def test_commit_command_comes_from_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Another hand-off command in the config is the argv the task runs, so
    # the program and the argument shape are not values of the module, and
    # the collector service runs the same configured command.
    _write_vault(tmp_path)
    calls, temp_path, _captured = _install_fakes(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    ctx = replace(
        ctx,
        config=replace(
            ctx.config,
            system_metrics_setup=replace(
                ctx.config.system_metrics_setup,
                command_path=Path("/opt/pyntara/hand-over"),
                commit_command=("/bin/sh", "-c", "{command_path} {file}"),
            ),
        ),
    )
    result = commit_final_system_metrics.task(ctx)
    assert result.success is True
    assert calls == [["/bin/sh", "-c", f"/opt/pyntara/hand-over {temp_path}"]]


def test_empty_commit_command_reports_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty commit_command is a broken config: the task reports it and
    # never copies the vault or builds the PDF, so no temporary file is
    # left behind.
    vault = _write_vault(tmp_path)
    calls, temp_path, _captured = _install_fakes(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path)
    ctx = replace(
        ctx,
        config=replace(
            ctx.config,
            system_metrics_setup=replace(
                ctx.config.system_metrics_setup, commit_command=()
            ),
        ),
    )
    result = commit_final_system_metrics.task(ctx)
    assert result.success is True
    assert any("commit_command" in warning for warning in result.warnings)
    assert calls == []
    assert not temp_path.exists()
    assert vault.read_bytes() == b"vault-bytes"


def test_missing_vault_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No runtime vault means nothing to back up: the reason is a warning
    # and the commit command never runs for the vault. The PDF is skipped
    # because no report is in the queue.
    calls, _, _ = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert any("missing" in warning for warning in result.warnings)
    assert calls == []


def test_empty_vault_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty runtime vault carries no data: the reason is a warning and
    # the commit command never runs for the vault.
    _write_vault(tmp_path, b"")
    calls, _, _ = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert any("empty" in warning for warning in result.warnings)
    assert calls == []


def test_commit_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A nonzero commit exit is a warning carrying the command detail; the
    # temp copy is removed.
    _write_vault(tmp_path)
    calls, temp_path, _ = _install_fakes(monkeypatch, tmp_path, fail=True)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert any("commit failed" in warning for warning in result.warnings)
    assert any("boom" in warning for warning in result.warnings)
    assert len(calls) == 1
    assert not temp_path.exists()


def test_commit_timeout_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A timed-out commit is a warning; the temp copy is removed.
    _write_vault(tmp_path)
    calls, temp_path, _ = _install_fakes(monkeypatch, tmp_path, timeout=True)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert any("commit failed" in warning for warning in result.warnings)
    assert len(calls) == 1
    assert not temp_path.exists()


def test_commits_pdf_from_queue_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The latest report in the queue is read and a PDF is built from it
    # and committed; the vault is also committed. Both calls use the
    # configured commit command.
    _write_vault(tmp_path)
    _write_queue_report(tmp_path, "lusab-babad", {"generated_at": "now"})
    pdf_bytes = b"encrypted-pdf"
    monkeypatch.setattr(
        "pyntara.telemetry_pdf.build",
        lambda cfg, report, hostname: pdf_bytes,
    )
    calls, vault_temp, _captured = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "lusab-babad.kdbx" in result.message
    assert "network-lusab-babad.pdf" in result.message
    pdf_temp = tmp_path / "network-lusab-babad.pdf"
    assert calls == [
        ["/usr/local/bin/commit_system_metrics", str(vault_temp)],
        ["/usr/local/bin/commit_system_metrics", str(pdf_temp)],
    ]
    assert not vault_temp.exists()
    assert not pdf_temp.exists()


def test_pdf_skipped_when_no_report_in_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # When no report is found in the queue, the PDF is skipped without
    # a warning; the vault is still committed.
    _write_vault(tmp_path)
    calls, _, _ = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "lusab-babad.kdbx" in result.message
    assert len(calls) == 1


def test_pdf_build_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A PDF build that returns None is a warning; the vault is still
    # committed.
    _write_vault(tmp_path)
    _write_queue_report(tmp_path, "lusab-babad", {"generated_at": "now"})
    monkeypatch.setattr(
        "pyntara.telemetry_pdf.build",
        lambda cfg, report, hostname: None,
    )
    calls, _, _ = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "lusab-babad.kdbx" in result.message
    assert any("PDF" in warning for warning in result.warnings)
    assert len(calls) == 1


def test_pdf_build_exception_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A PDF build that raises is a warning; the vault is still committed.
    _write_vault(tmp_path)
    _write_queue_report(tmp_path, "lusab-babad", {"generated_at": "now"})
    monkeypatch.setattr(
        "pyntara.telemetry_pdf.build",
        lambda cfg, report, hostname: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    calls, _, _ = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "lusab-babad.kdbx" in result.message
    assert any("PDF" in warning for warning in result.warnings)
    assert len(calls) == 1


def test_pdf_commit_failure_is_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed PDF commit is a warning; the vault is still committed.
    _write_vault(tmp_path)
    _write_queue_report(tmp_path, "lusab-babad", {"generated_at": "now"})
    monkeypatch.setattr(
        "pyntara.telemetry_pdf.build",
        lambda cfg, report, hostname: b"pdf",
    )

    def fail_pdf_commit(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        if command and command[-1].endswith(".pdf"):
            return _FakeProc(1, "", "pdf commit boom")
        return _FakeProc(0)

    calls: list[list[str]] = []

    def record_and_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        return fail_pdf_commit(command, **kwargs)

    monkeypatch.setattr("pyntara.utils.subprocess.run", record_and_run)
    monkeypatch.setattr(socket, "gethostname", lambda: "lusab-babad")
    monkeypatch.setattr(
        commit_final_system_metrics.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "lusab-babad.kdbx" in result.message
    assert any("PDF" in warning for warning in result.warnings)
    assert len(calls) == 2


def test_vault_missing_pdf_still_committed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A missing vault is a warning but the PDF is still built from the
    # report and committed.
    _write_queue_report(tmp_path, "lusab-babad", {"generated_at": "now"})
    pdf_bytes = b"encrypted-pdf"
    monkeypatch.setattr(
        "pyntara.telemetry_pdf.build",
        lambda cfg, report, hostname: pdf_bytes,
    )
    calls, _, _ = _install_fakes(monkeypatch, tmp_path)
    result = commit_final_system_metrics.task(_ctx(tmp_path))
    assert result.success is True
    assert result.changed is True
    assert result.message is not None
    assert "network-lusab-babad.pdf" in result.message
    assert any("missing" in warning for warning in result.warnings)
    assert len(calls) == 1


def test_catalog_has_commit_final_last_in_every_mode() -> None:
    # The task must run after every other default task of a mode, so the
    # runtime vault exists and the queue machinery is deployed before the
    # backup and the PDF are committed.
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
