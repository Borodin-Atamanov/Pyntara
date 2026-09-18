"""Encrypted telemetry PDF: the machine connection card.

The report collector writes the report as network-<hostname>.json and
commits it through the queue. This module additionally builds a readable
connection card from the same report and the configured runtime vault
entries: the hostname, the NextDNS profile, every working ssh command,
the machine secrets and the raw report at the bottom. It renders the
card into a monospace PDF, encrypts it with AES-256 using the telemetry
password entry of the runtime vault, and returns the encrypted bytes;
the unencrypted PDF exists only in memory (docs/spec/system-metrics.md,
section Telemetry PDF). The module imports pyntara.metrics by module and
reads open_runtime_vault through the attribute, the same deferred
pattern as pyntara.metrics_send, so the two modules can import each
other.

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
from pyntara.logger import log_progress as _log
from pyntara.values import engine as engine_values
from pyntara.values import system_metrics_setup as values


def _ssh_commands(report: dict[str, object]) -> list[str]:
    """Every working ssh command of the report, in order.

    A record carries the command under the name the declared report
    vocabulary maps from the meaning ssh (REPORT_RECORD_KEYS). A module may
    print one record as an object or many as an array, so both shapes are
    read. A record whose scope is host (the loopback) or link (a link-local
    address) can never connect and is skipped; the words come from the
    declared values, like every other vocabulary of the report.
    """

    ssh_key = engine_values.REPORT_RECORD_KEYS["ssh"]
    scope_key = engine_values.REPORT_RECORD_KEYS["scope"]
    host_scope = engine_values.HOST_SCOPE_NAME
    link_scope = engine_values.LINK_SCOPE_NAME
    keys = values.COLLECTOR.report_keys
    commands: list[str] = []
    seen: set[str] = set()
    for section_key in (keys["network"], keys["system"]):
        section = report.get(section_key, [])
        if not isinstance(section, list):
            continue
        for module in section:
            if not isinstance(module, dict):
                continue
            output = module.get(keys["output"])
            records: list[object] = []
            if isinstance(output, dict):
                records.append(output)
            elif isinstance(output, list):
                records.extend(output)
            for record in records:
                if not isinstance(record, dict):
                    continue
                scope = record.get(scope_key)
                if scope in (host_scope, link_scope):
                    continue
                command = record.get(ssh_key)
                if command:
                    text = str(command)
                    if text not in seen:
                        seen.add(text)
                        commands.append(text)
    return commands


def _nextdns_id(report: dict[str, object]) -> str | None:
    """The NextDNS profile ID of the report, or None when absent.

    The declared module name prints the applied profile ID as plain text;
    the PDF shows it next to the hostname so the operator sees which
    profile filters the machine DNS.
    """

    keys = values.COLLECTOR.report_keys
    module_name = values.TELEMETRY_PDF.nextdns_module_name
    section = report.get(keys["network"], [])
    if not isinstance(section, list):
        return None
    for module in section:
        if not isinstance(module, dict):
            continue
        if module.get(keys["name"]) != module_name:
            continue
        output = module.get(keys["output"])
        if isinstance(output, str) and output.strip():
            return output.strip()
    return None


def _read_vault_entries(
    kp: Any, titles: tuple[str, ...], field_order: tuple[str, ...]
) -> list[tuple[str, list[str]]]:
    """The configured runtime vault entries with their values.

    The values are printed in the configured field order without labels,
    so the operator reads them directly. An entry that is absent or empty
    is skipped, and an empty field leaves no line.
    """

    entries: list[tuple[str, list[str]]] = []
    for title in titles:
        entry = kp.find_entries(
            title=title, group=kp.root_group, recursive=False, first=True
        )
        if entry is None:
            continue
        values: list[str] = []
        for field in field_order:
            text = str(getattr(entry, field) or "").strip()
            if text:
                values.append(text)
        if values:
            entries.append((title, values))
    return entries


def _top_level_spaces(command: str) -> list[int]:
    """Positions of spaces outside double quotes in a shell command.

    These are the only places a wrapped command may break: a backslash
    inside a quoted value would become part of the value, so the break
    points are the top-level word separators only.
    """

    points: list[int] = []
    in_quotes = False
    for index, char in enumerate(command):
        if char == '"':
            in_quotes = not in_quotes
        elif char == " " and not in_quotes:
            points.append(index)
    return points


def _wrap_command(command: str, width: int) -> list[str]:
    """The shell command split into backslash-joined lines no wider than width.

    Each line except the last ends with a space and a backslash, so
    pasting the copied lines into a shell rebuilds the command exactly;
    the break falls on a top-level space, never inside a quoted value. A
    command that fits one line is returned unchanged.
    """

    if len(command) <= width:
        return [command]
    points = _top_level_spaces(command)
    pieces: list[str] = []
    start = 0
    while start < len(command):
        limit = start + width
        cut = start
        for point in points:
            if point <= start:
                continue
            if point > limit:
                break
            cut = point
        if cut == start:
            cut = min(limit, len(command))
        pieces.append(command[start:cut].rstrip())
        if cut < len(command) and command[cut] == " ":
            start = cut + 1
        else:
            start = cut
    return [piece + " \\" for piece in pieces[:-1]] + [pieces[-1]]


def build_text(
    report: dict[str, object],
    entries: list[tuple[str, list[str]]],
    hostname: str,
) -> str:
    """The PDF content: hostname, nextdns, ssh commands, secrets, JSON.

    The first line is the machine hostname and the collection moment, the
    title of the document as well. The ssh commands sit one per copyable
    line, long ones joined by backslashes; the secrets follow with their
    values and no labels; the raw report ends the document verbatim, so
    the whole network.json is always present at the bottom.
    """

    keys = values.COLLECTOR.report_keys
    pdf_cfg = values.TELEMETRY_PDF
    lines: list[str] = []
    lines.append(f"{hostname} {report.get(keys['generated_at'], '')}")
    lines.append("")
    nextdns_id = _nextdns_id(report)
    if nextdns_id:
        lines.append(f"{pdf_cfg.nextdns_module_name} {nextdns_id}")
        lines.append("")
    lines.append(pdf_cfg.section_ssh)
    commands = _ssh_commands(report)
    if commands:
        for command in commands:
            lines.extend(_wrap_command(command, pdf_cfg.line_width_chars))
    else:
        lines.append("(no ssh addresses)")
    lines.append("")
    lines.append(pdf_cfg.section_secrets)
    if entries:
        for title, entry_values in entries:
            lines.append(title)
            for value in entry_values:
                lines.extend(value.split("\n"))
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


def render(text: str, pdf_cfg: values.TelemetryPdf, title: str) -> bytes:
    """Render the text into a monospace PDF, in memory.

    The title becomes the document title, so a viewer names the file by
    the hostname and the moment instead of untitled. The base font needs
    no embedding and opens in any viewer, including a phone; a page break
    is inserted when the text reaches the bottom margin.
    """

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setTitle(title)
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


def build(report: dict[str, object], hostname: str) -> bytes | None:
    """The encrypted telemetry PDF bytes, or None when it cannot be built.

    The password comes from the telemetry_password_entry_title entry of
    the runtime vault and never appears in any message. Every failure is
    journaled at the System Metrics error priority and returns None, so
    the caller commits network.json without the PDF.
    """

    kp = pyntara.metrics.open_runtime_vault()
    if kp is None:
        return None
    title = values.TELEMETRY_PASSWORD_ENTRY_TITLE
    entry = kp.find_entries(
        title=title, group=kp.root_group, recursive=False, first=True
    )
    password = (entry.password or "").strip() if entry is not None else ""
    if not password:
        _log(
            f"telemetry pdf skipped: no password in vault entry {title!r}",
            priority=values.ERROR_PRIORITY,
        )
        return None
    entries = _read_vault_entries(
        kp,
        values.TELEMETRY_PDF_VAULT_ENTRY_TITLES,
        values.TELEMETRY_PDF.field_order,
    )
    try:
        text = build_text(report, entries, hostname)
        generated_at = report.get(
            values.COLLECTOR.report_keys["generated_at"], ""
        )
        title = f"{hostname} {generated_at}"
        return encrypt(render(text, values.TELEMETRY_PDF, title), password)
    except Exception as exc:  # noqa: BLE001 - any failure skips the PDF only
        _log(f"telemetry pdf skipped: {exc}", priority=values.ERROR_PRIORITY)
        return None
