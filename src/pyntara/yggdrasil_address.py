"""Command: the yggdrasil self address of this machine and its ssh command.

The command asks the running yggdrasil daemon through the configured
admin socket call (yggdrasilctl -json getSelf by default), parses the
JSON with the standard library and prints one JSON record: the self
address, the sshd port and the ssh command that reaches the SSH daemon
over the overlay. When the live source fails, the saved address file
written by the yggdrasil_service_setup task is the fallback: the record
then carries the reason as a note, so a collector that keeps the document
keeps the error instead of losing it. When neither source yields an
address, the command exits nonzero with the reason and the raw output of
the query on stderr.

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

from pyntara.ssh import ssh_port_from_directives
from pyntara.ssh_access import ssh_command
from pyntara.values import engine as engine_values
from pyntara.values import yggdrasil_service_setup as values
from pyntara.yggdrasil import self_address_from_output


def _live_self_address() -> tuple[str | None, str]:
    """The (self address, reason) from the configured admin socket call.

    The command and the address field come from the
    [yggdrasil_service_setup] table, which the task uses as well, so the
    task and the command read the node address the same way. A failed
    call, a nonzero exit or an unparsable output yields (None, reason)
    with the raw utility output kept, so the caller can report it as is.
    """

    try:
        result = subprocess.run(
            list(values.SELF_ADDRESS_COMMAND),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, f"cannot run the self address query: {exc}"
    output = result.stdout
    if result.returncode != 0:
        combined = f"{output}\n{result.stderr}".strip()
        return None, f"the self address query exited {result.returncode}: {combined}"
    address = self_address_from_output(output, values.ADMIN_OUTPUT_KEYS["address"])
    if address is None:
        return None, f"cannot parse the self address output: {output.strip()}"
    return address, ""


def access_record() -> tuple[dict[str, object] | None, str]:
    """The report record of the yggdrasil channel, or (None, reason).

    The live admin socket query is the primary source; the saved address
    file is the fallback, because the daemon may be down at collection
    time. A fallback keeps the live reason as a note, so the report shows
    why the live source failed instead of hiding it. The section is
    checked before either source is used, so a config that lacks a key of
    this channel is named in one line instead of producing a record with
    an empty value.
    """

    address, reason = _live_self_address()
    note = ""
    if not address:
        try:
            saved = values.ADDRESS_FILE_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            saved = ""
        if saved:
            address = saved
            note = f"address read from the saved file: {reason}"
    if not address:
        return None, f"yggdrasil self address is not available: {reason}"
    try:
        port = ssh_port_from_directives()
    except RuntimeError as exc:
        return None, str(exc)
    keys = engine_values.REPORT_RECORD_KEYS
    record: dict[str, object] = {
        keys["channel"]: values.REPORT_CHANNEL_NAME,
        keys["address"]: address,
        keys["port"]: port,
        keys["ssh"]: ssh_command(address, port),
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
