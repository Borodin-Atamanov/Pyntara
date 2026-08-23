"""Shared Tor helpers: read the onion address of the SSH service.

The tor_setup task and the deployed address command
(pyntara.tor_address) both need the onion address Tor wrote into the
hidden service hostname file. The reading and trimming logic lives here,
shared and imported, never copied (architecture contract, Configuration).
"""

from __future__ import annotations

from pathlib import Path

from pyntara.utils import trim_whitespace


def onion_address_from_hostname_file(path: Path) -> str | None:
    """The onion address from a hidden service hostname file, or None.

    Tor writes the address as a single line with a trailing newline.
    The text crosses an external boundary, so it passes through the
    shared trim_whitespace helper before it is reported or stored
    (project rules, the trim rule). A missing file, a read error or an
    empty result yields None, so the caller falls back to the saved
    address file instead of failing.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    address = trim_whitespace(text)
    return address or None
