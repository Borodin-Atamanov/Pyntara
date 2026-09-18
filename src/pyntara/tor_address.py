"""Command: the Tor onion address of this machine and its ssh command.

The command reads the live hidden service hostname file and prints one
JSON record: the .onion address, the virtual port of the onion service,
the local SOCKS proxy of the Tor daemon and the ssh command that reaches
the SSH daemon through the onion service. When the hostname file is
missing or empty, the saved address file written by the tor_setup task
is the fallback: the record then carries a note with the reason, so a
collector that keeps the document keeps the error instead of losing it.
When neither source yields an address, the command exits nonzero with an
explanation on stderr.

The virtual port and the SOCKS port are declared values of the tor_setup
section, the same ones the task renders into the drop-in file, so the
report can never name a port the machine does not serve
(docs/spec/system-metrics.md, section Report collector). Runs as
`python -m pyntara.tor_address`, without an argument.
"""

from __future__ import annotations

import json
import sys

from pyntara.ssh_access import socks_proxy_address, ssh_command
from pyntara.tor import onion_address_from_hostname_file
from pyntara.values import engine as engine_values
from pyntara.values import tor_setup as values

# The identity may have been recreated between two provisioning runs
# without the task noticing, so the saved address file is the fallback of
# the live hostname file.
FALLBACK_NOTE = (
    "address read from the saved file, the hostname file is missing or empty"
)


def resolve_address() -> tuple[str, str]:
    """The (address, note) of the onion service.

    The live hostname file is the primary source; the saved address file
    is the fallback. An empty address means no source yielded one, and
    the caller reports the failure.
    """

    address = onion_address_from_hostname_file(
        values.HIDDEN_SERVICE_DIR / values.HOSTNAME_FILE_NAME
    )
    if address:
        return address, ""
    try:
        saved = values.ADDRESS_FILE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        return saved, FALLBACK_NOTE
    return "", ""


def access_record() -> tuple[dict[str, object] | None, str]:
    """The report record of the Tor channel, or (None, reason).

    Every value of the record is declared, so the virtual port of the
    onion service and the SOCKS proxy of the daemon are the ones the
    machine really serves.
    """

    address, note = resolve_address()
    if not address:
        return None, "Tor SSH onion address is not available"
    keys = engine_values.REPORT_RECORD_KEYS
    proxy = socks_proxy_address(values.SOCKS_PORT)
    record: dict[str, object] = {
        keys["channel"]: values.REPORT_CHANNEL_NAME,
        keys["address"]: address,
        keys["port"]: values.ONION_SSH_PORT,
        keys["proxy"]: proxy,
        keys["ssh"]: ssh_command(address, values.ONION_SSH_PORT, proxy),
    }
    if note:
        record[keys["note"]] = note
    return record, ""


def main(argv: list[str]) -> int:
    """Print the record; 0 when found, 2 on a usage error, 1 otherwise."""

    if len(argv) != 1:
        print(f"usage: {argv[0]}", file=sys.stderr)
        return 2
    record, error = access_record()
    if record is None:
        print(error, file=sys.stderr)
        return 1
    print(
        json.dumps(
            record,
            ensure_ascii=False,
            indent=engine_values.REPORT_JSON_INDENT,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
