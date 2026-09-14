"""Unit tests for the encrypted telemetry PDF.

The tests cover the behavior that must hold and nothing about the exact
layout: the PDF encrypts with AES-256 and opens only with the right
password, the ssh commands of the report reach the PDF text, and the
collector commits the report even when the PDF cannot be built.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pikepdf
import pytest
from support import make_config

from pyntara import metrics_collect, telemetry_pdf

REPORT_KEYS = make_config().system_metrics_setup.collector.report_keys


def _report(**modules: object) -> dict[str, object]:
    """A report body in the shape the collector builds."""

    keys = REPORT_KEYS
    return {
        keys["generated_at"]: "2026-09-14-12-00-00",
        keys["ready_percent"]: 100,
        keys["network"]: [
            {
                keys["name"]: "ipv4",
                keys["status"]: "ok",
                keys["output"]: modules.get(
                    "ipv4", [{"ssh": "ssh -v -p 30222 192.168.1.5"}]
                ),
            },
        ],
        keys["system"]: [
            {
                keys["name"]: "hostname",
                keys["status"]: "ok",
                keys["output"]: modules.get("hostname", "testhost"),
            },
        ],
    }


def test_encrypt_opens_with_password_and_rejects_wrong() -> None:
    """The PDF is AES-256 and opens only with the right password."""

    raw = telemetry_pdf.render(
        "hello", make_config().system_metrics_setup.telemetry_pdf
    )
    encrypted = telemetry_pdf.encrypt(raw, "secretpw")
    opened = pikepdf.open(io.BytesIO(encrypted), password="secretpw")
    assert len(opened.pages) == 1
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(io.BytesIO(encrypted), password="wrong")


def test_build_text_carries_the_ssh_commands() -> None:
    """Every ssh command of the report reaches the PDF text."""

    cfg = make_config()
    text = telemetry_pdf.build_text(cfg, _report(), [], "testhost")
    assert "ssh -v -p 30222 192.168.1.5" in text


def test_commit_telemetry_pdf_never_raises_when_build_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken PDF build drops only the PDF, never the report."""

    cfg = make_config(
        task_data_root=tmp_path,
        system_metrics_command_path=tmp_path / "commit",
    )
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.metrics_collect.subprocess.run", fake_run)

    def broken_build(*args: object, **kwargs: object) -> bytes:
        del args, kwargs
        raise RuntimeError("boom")

    monkeypatch.setattr("pyntara.telemetry_pdf.build", broken_build)
    monkeypatch.setattr(
        "pyntara.metrics_collect.socket.gethostname", lambda: "testhost"
    )
    metrics_collect._commit_telemetry_pdf(cfg, _report())
    assert calls == []


def test_commit_telemetry_pdf_commits_the_pdf_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The built PDF bytes are committed under the pdf file name."""

    commit_path = tmp_path / "commit"
    cfg = make_config(
        task_data_root=tmp_path,
        system_metrics_command_path=commit_path,
    )
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.metrics_collect.subprocess.run", fake_run)
    monkeypatch.setattr(
        "pyntara.telemetry_pdf.build", lambda *a, **k: b"encrypted-bytes"
    )
    monkeypatch.setattr(
        "pyntara.metrics_collect.socket.gethostname", lambda: "testhost"
    )
    metrics_collect._commit_telemetry_pdf(cfg, _report())
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == str(commit_path)
    assert argv[1].endswith("network-testhost.pdf")
    assert not Path(argv[1]).exists()
