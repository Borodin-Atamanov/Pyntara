"""Command: print the yggdrasil self address of this machine.

The command asks the running yggdrasil daemon through yggdrasilctl
getSelf, parses the JSON with the standard library and prints the self
address on stdout. When the live source fails, the saved address file
written by the yggdrasil_service_setup task is the fallback: the
address is printed first and the reason on the following stdout line,
so a collector that takes the stdout keeps the error instead of losing
it. When neither source yields an address, the command exits nonzero
with the reason and the raw yggdrasilctl output on stderr. The command
takes the saved address file path as its only argument and needs no
config access (docs/spec/yggdrasil-service.md).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pyntara.yggdrasil import self_address_from_output


def _live_self_address() -> tuple[str | None, str]:
    """The (self address, reason) from yggdrasilctl getSelf.

    A failed call, a nonzero exit or an unparsable output yields
    (None, reason) with the raw utility output kept, so the caller can
    report it as is.
    """

    try:
        result = subprocess.run(
            ["yggdrasilctl", "-json", "getSelf"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, f"cannot run yggdrasilctl: {exc}"
    output = result.stdout
    if result.returncode != 0:
        combined = f"{output}\n{result.stderr}".strip()
        return None, f"yggdrasilctl exited {result.returncode}: {combined}"
    address = self_address_from_output(output)
    if address is None:
        return None, f"cannot parse yggdrasilctl output: {output.strip()}"
    return address, ""


def main(argv: list[str]) -> int:
    """Print the address; 0 when found, 2 on a usage error, 1 otherwise.

    The live admin socket query is the primary source; the saved address
    file is the fallback, because the daemon may be down at collection
    time. The fallback prints the reason on the following stdout line so
    the collector keeps it; a total failure prints the reason with the
    raw output on stderr and exits nonzero, so the collector reports the
    error as the module output.
    """

    if len(argv) != 2:
        print(f"usage: {argv[0]} ADDRESS_FILE_PATH", file=sys.stderr)
        return 2
    address, reason = _live_self_address()
    if address:
        print(address)
        return 0
    try:
        saved = Path(argv[1]).read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        print(saved)
        print(f"address read from the saved file: {reason}")
        return 0
    print(f"yggdrasil self address is not available: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
