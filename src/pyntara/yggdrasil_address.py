"""Command: the yggdrasil self address of this machine and its ssh command.

The command asks the running yggdrasil daemon through yggdrasilctl
getSelf, parses the JSON with the standard library and prints one JSON
record: the self address, the sshd port and the ssh command that reaches
the SSH daemon over the overlay. When the live source fails, the saved
address file written by the yggdrasil_service_setup task is the
fallback: the record then carries the reason as a note, so a collector
that keeps the document keeps the error instead of losing it. When
neither source yields an address, the command exits nonzero with the
reason and the raw yggdrasilctl output on stderr.

The sshd port comes from the single system config the command is given,
which is the same source the SSH daemon task writes, so the report can
never name a port the daemon does not listen on
(docs/spec/system-metrics.md, section Report collector). Runs as
`python -m pyntara.yggdrasil_address CONFIG_PATH`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pyntara.config import (
    Config,
    absent_config_keys,
    load_config,
)
from pyntara.ssh import ssh_port_from_directives
from pyntara.ssh_access import ssh_command
from pyntara.yggdrasil import self_address_from_output

# The channel name the record carries.
CHANNEL = "yggdrasil"


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


def access_record(cfg: Config) -> tuple[dict[str, object] | None, str]:
    """The report record of the yggdrasil channel, or (None, reason).

    The live admin socket query is the primary source; the saved address
    file is the fallback, because the daemon may be down at collection
    time. A fallback keeps the live reason as a note, so the report shows
    why the live source failed instead of hiding it.
    """

    setup = cfg.yggdrasil_service_setup
    address, reason = _live_self_address()
    note = ""
    if not address:
        missing = absent_config_keys(setup, ("address_file_path",))
        if missing:
            return None, (
                "the yggdrasil_service_setup section of the config has no "
                + ", ".join(missing)
                + f", so the saved address cannot be read: {reason}"
            )
        try:
            saved = setup.address_file_path.read_text(encoding="utf-8").strip()
        except OSError:
            saved = ""
        if saved:
            address = saved
            note = f"address read from the saved file: {reason}"
    if not address:
        return None, f"yggdrasil self address is not available: {reason}"
    try:
        port = ssh_port_from_directives(cfg.ssh_daemon_setup.directives)
    except RuntimeError as exc:
        return None, str(exc)
    record: dict[str, object] = {
        "channel": CHANNEL,
        "address": address,
        "port": port,
        "ssh": ssh_command(address, port),
    }
    if note:
        record["note"] = note
    return record, ""


def main(argv: list[str]) -> int:
    """Print the record; 0 when found, 2 on a usage error, 1 otherwise."""

    if len(argv) != 2:
        print(f"usage: {argv[0]} CONFIG_PATH", file=sys.stderr)
        return 2
    cfg = load_config(Path(argv[1]))
    record, error = access_record(cfg)
    if record is None:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
