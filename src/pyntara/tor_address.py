"""Command: print the Tor SSH onion address of this machine.

The command reads the live hidden service hostname file and prints the
.onion address on stdout. When the hostname file is missing or empty,
the saved address file written by the tor_setup task is the fallback:
the address is printed first and the reason on the following stdout
line, so a collector that takes the stdout keeps the error instead of
losing it. When neither source yields an address, the command exits
nonzero with an explanation on stderr. The command takes the hidden
service directory and the saved address file path as positional
arguments and needs no config access (docs/spec/tor-service.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

from pyntara.tor import onion_address_from_hostname_file


def main(argv: list[str]) -> int:
    """Print the address; 0 when found, 2 on a usage error, 1 otherwise.

    The live hostname file is the primary source; the saved address
    file is the fallback, because the identity may have been recreated
    between two provisioning runs without the task noticing. When the
    fallback is used, the reason goes to the following stdout line so
    the collector keeps it; a total failure prints the reason on stderr
    and exits nonzero.
    """

    if len(argv) != 3:
        print(f"usage: {argv[0]} HIDDEN_SERVICE_DIR ADDRESS_FILE_PATH", file=sys.stderr)
        return 2
    address = onion_address_from_hostname_file(Path(argv[1]) / "hostname")
    if address:
        print(address)
        return 0
    try:
        saved = Path(argv[2]).read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        print(saved)
        print(
            "address read from the saved file, the hostname file is "
            "missing or empty"
        )
        return 0
    print("Tor SSH onion address is not available", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
