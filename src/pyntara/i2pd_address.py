"""Command: print the I2P tunnel address of this machine.

The command decodes the live i2pd keys file of the SSH tunnel and prints
the .b32.i2p address on stdout. When the keys file is missing or broken,
the saved address file written by the i2pd_service_setup task is the
fallback; when neither source yields an address, the command exits
nonzero with an explanation on stderr. The stdout stays a single address
line, so the System Metrics collector can use the command as a module
(docs/spec/i2pd-service.md). The command takes the keys path and the
saved address file path as positional arguments and needs no config
access.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pyntara.i2pd import b32_address


def main(argv: list[str]) -> int:
    """Print the address; 0 when found, 2 on a usage error, 1 otherwise.

    The keys file is the primary source; the saved address file is the
    fallback, because the identity may have been recreated between two
    provisioning runs without the task noticing. When the fallback is
    used, a note goes to stderr so a manual call shows the source while
    the stdout stays clean for the collector.
    """

    if len(argv) != 3:
        print(f"usage: {argv[0]} KEYS_PATH ADDRESS_FILE_PATH", file=sys.stderr)
        return 2
    address = b32_address(Path(argv[1]))
    if address:
        print(address)
        return 0
    try:
        saved = Path(argv[2]).read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        print(
            "address read from the saved file, the keys file is missing "
            "or broken",
            file=sys.stderr,
        )
        print(saved)
        return 0
    print("I2P tunnel address is not available", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
