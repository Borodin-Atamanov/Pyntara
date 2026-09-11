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

The virtual port and the SOCKS port come from the single system config
the command is given, which is the same source the tor_setup task writes
into the drop-in file, so the report can never name a port the machine
does not serve (docs/spec/system-metrics.md, section Report collector).
Runs as `python -m pyntara.tor_address CONFIG_PATH`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pyntara.config import (
    Config,
    absent_config_keys,
    load_config,
)
from pyntara.ssh_access import socks_proxy_address, ssh_command
from pyntara.tor import onion_address_from_hostname_file

# The channel name the record carries.
CHANNEL = "tor"

# The identity may have been recreated between two provisioning runs
# without the task noticing, so the saved address file is the fallback of
# the live hostname file.
FALLBACK_NOTE = (
    "address read from the saved file, the hostname file is missing or empty"
)


def resolve_address(hidden_service_dir: Path, saved_path: Path) -> tuple[str, str]:
    """The (address, note) of the onion service.

    The live hostname file is the primary source; the saved address file
    is the fallback. An empty address means no source yielded one, and
    the caller reports the failure.
    """

    address = onion_address_from_hostname_file(hidden_service_dir / "hostname")
    if address:
        return address, ""
    try:
        saved = saved_path.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        return saved, FALLBACK_NOTE
    return "", ""


def access_record(cfg: Config) -> tuple[dict[str, object] | None, str]:
    """The report record of the Tor channel, or (None, reason).

    Every value of the record comes from the config, so the virtual port
    of the onion service and the SOCKS proxy of the daemon are the ones
    the machine really serves.
    """

    setup = cfg.tor_setup
    missing = absent_config_keys(
        setup,
        ("hidden_service_dir", "address_file_path", "socks_port", "onion_ssh_port"),
    )
    if missing:
        return None, (
            "the tor_setup section of the config has no " + ", ".join(missing)
        )
    address, note = resolve_address(setup.hidden_service_dir, setup.address_file_path)
    if not address:
        return None, "Tor SSH onion address is not available"
    proxy = socks_proxy_address(setup.socks_port)
    record: dict[str, object] = {
        "channel": CHANNEL,
        "address": address,
        "port": setup.onion_ssh_port,
        "proxy": proxy,
        "ssh": ssh_command(address, setup.onion_ssh_port, proxy),
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
