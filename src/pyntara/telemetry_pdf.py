"""Encrypted telemetry PDF: the report and the machine secrets in one file.

The report collector writes the report as network-<hostname>.json and
commits it through the queue. This module additionally renders the same
report and the configured runtime vault entries into a monospace PDF,
encrypts it with AES-256 using the telemetry password entry of the
runtime vault, and returns the encrypted bytes; the unencrypted PDF
exists only in memory (docs/spec/system-metrics.md, section Telemetry
PDF). The module imports pyntara.metrics by module and reads
open_runtime_vault through the attribute, the same deferred pattern as
pyntara.metrics_send, so the two modules can import each other.

The build function never raises: a missing vault, a missing password or
any rendering or encryption failure is journaled and None is returned,
so the collector keeps committing network.json without the PDF.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pikepdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import pyntara.metrics
from pyntara.config import Config, TelemetryPdfConfig
from pyntara.logger import log_progress as _log


def _ssh_commands(cfg: Config, report: dict[str, object]) -> list[str]:
    """Every ssh command of the report records, in order, one per line.

    A record carries the command under the name the engine config maps
    from the meaning ssh (report_record_keys), so the reader of the
    section sees exactly the commands the address commands printed. Text
    module outputs contribute nothing: they carry no record with a
    command.
    """

    ssh_key = cfg.engine.report_record_keys["ssh"]
    keys = cfg.system_metrics_setup.collector.report_keys
    commands: list[str] = []
    for section_key in (keys["network"], keys["system"]):
        section = report.get(section_key, [])
        if not isinstance(section, list):
            continue
        for module in section:
            if not isinstance(module, dict):
                continue
            output = module.get(keys["output"])
            if not isinstance(output, list):
                continue
            for record in output:
                if not isinstance(record, dict):
                    continue
                command = record.get(ssh_key)
                if command:
                    commands.append(str(command))
    return commands


def _module_lines(
    modules: object, keys: dict[str, str]
) -> list[str]:
    """Readable lines of a module section: name, status and output.

    A structured output is printed as indented JSON, text output as is,
    so the same content that travels in network.json stays readable.
    """

    lines: list[str] = []
    if not isinstance(modules, list):
        return lines
    for module in modules:
        if not isinstance(module, dict):
            continue
        name = module.get(keys["name"], "")
        status = module.get(keys["status"], "")
        output = module.get(keys["output"], "")
        lines.append(f"{name} ({status})")
        if isinstance(output, (dict, list)):
            lines.append(json.dumps(output, ensure_ascii=False, indent=2))
        elif output:
            lines.append(str(output))
    return lines


def _read_vault_entries(
    kp: Any, titles: tuple[str, ...], field_order: tuple[str, ...]
) -> list[tuple[str, list[tuple[str, str]]]]:
    """The configured runtime vault entries with their filled fields.

    An entry that is absent or empty is skipped, so a machine without a
    secret simply omits it. The values are the ones the operator needs,
    nothing is invented.
    """

    entries: list[tuple[str, list[tuple[str, str]]]] = []
    for title in titles:
        entry = kp.find_entries(
            title=title, group=kp.root_group, recursive=False, first=True
        )
        if entry is None:
            continue
        fields: list[tuple[str, str]] = []
        for field in field_order:
            value = getattr(entry, field)
            if value:
                fields.append((field, str(value).strip()))
        if fields:
            entries.append((title, fields))
    return entries


def build_text(
    cfg: Config,
    report: dict[str, object],
    entries: list[tuple[str, list[tuple[str, str]]]],
    hostname: str,
) -> str:
    """The PDF content as plain text: header, ssh, modules, secrets, JSON.

    The ssh commands sit first on their own lines for copy convenience;
    the raw report ends the document verbatim as indented JSON, so the
    whole network.json is always present at the bottom.
    """

    keys = cfg.system_metrics_setup.collector.report_keys
    pdf_cfg = cfg.system_metrics_setup.telemetry_pdf
    lines: list[str] = []
    lines.append("Pyntara telemetry")
    lines.append(f"hostname: {hostname}")
    lines.append(f"generated at: {report.get(keys['generated_at'], '')}")
    lines.append(f"format: {pdf_cfg.format_version}")
    lines.append("")
    lines.append(pdf_cfg.section_ssh)
    commands = _ssh_commands(cfg, report)
    if commands:
        lines.extend(commands)
    else:
        lines.append("(no ssh addresses)")
    lines.append("")
    lines.append(pdf_cfg.section_network)
    lines.extend(_module_lines(report.get(keys["network"]), keys))
    lines.append("")
    lines.append(pdf_cfg.section_system)
    lines.extend(_module_lines(report.get(keys["system"]), keys))
    lines.append("")
    lines.append(pdf_cfg.section_secrets)
    if entries:
        for title, fields in entries:
            lines.append(title)
            for label, value in fields:
                lines.append(f"{label}: {value}")
            lines.append("")
    else:
        lines.append("(no machine secrets)")
    lines.append("")
    lines.append(pdf_cfg.section_json)
    lines.append(json.dumps(report, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def _wrapped_lines(text: str, line_width_chars: int) -> list[str]:
    """The text split into lines no longer than the configured width.

    A long line is broken at the last space before the limit, so an ssh
    command wraps at a space and stays copyable as one command.
    """

    out: list[str] = []
    for line in text.split("\n"):
        if not line:
            out.append("")
            continue
        while len(line) > line_width_chars:
            cut = line.rfind(" ", 0, line_width_chars)
            if cut <= 0:
                cut = line_width_chars
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        out.append(line)
    return out


def render(text: str, pdf_cfg: TelemetryPdfConfig) -> bytes:
    """Render the text into a monospace PDF, in memory.

    The base font needs no embedding and opens in any viewer, including
    a phone; a page break is inserted when the text reaches the bottom
    margin.
    """

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setFont(pdf_cfg.font, pdf_cfg.font_size)
    y = A4[1] - pdf_cfg.margin
    leading = pdf_cfg.font_size * 1.2
    for line in _wrapped_lines(text, pdf_cfg.line_width_chars):
        pdf.drawString(pdf_cfg.margin, y, line)
        y -= leading
        if y < pdf_cfg.margin:
            pdf.showPage()
            pdf.setFont(pdf_cfg.font, pdf_cfg.font_size)
            y = A4[1] - pdf_cfg.margin
    pdf.save()
    return buf.getvalue()


def encrypt(raw: bytes, password: str) -> bytes:
    """Encrypt the PDF bytes with AES-256, in memory.

    revision 6 is the PDF standard AES-256 encryption. The user and the
    owner password are the same, so the file opens and copies with one
    password.
    """

    pdf = pikepdf.open(io.BytesIO(raw))
    out = io.BytesIO()
    pdf.save(
        out,
        encryption=pikepdf.Encryption(user=password, owner=password, R=6),
    )
    return out.getvalue()


def build(cfg: Config, report: dict[str, object], hostname: str) -> bytes | None:
    """The encrypted telemetry PDF bytes, or None when it cannot be built.

    The password comes from the telemetry_password_entry_title entry of
    the runtime vault and never appears in any message. Every failure is
    journaled at the System Metrics error priority and returns None, so
    the caller commits network.json without the PDF.
    """

    sms = cfg.system_metrics_setup
    kp = pyntara.metrics.open_runtime_vault(cfg)
    if kp is None:
        return None
    title = sms.telemetry_password_entry_title
    entry = kp.find_entries(
        title=title, group=kp.root_group, recursive=False, first=True
    )
    password = (entry.password or "").strip() if entry is not None else ""
    if not password:
        _log(
            f"telemetry pdf skipped: no password in vault entry {title!r}",
            priority=sms.error_priority,
        )
        return None
    entries = _read_vault_entries(
        kp, sms.telemetry_pdf_vault_entry_titles, sms.telemetry_pdf.field_order
    )
    try:
        text = build_text(cfg, report, entries, hostname)
        return encrypt(render(text, sms.telemetry_pdf), password)
    except Exception as exc:  # noqa: BLE001 - any failure skips the PDF only
        _log(f"telemetry pdf skipped: {exc}", priority=sms.error_priority)
        return None
