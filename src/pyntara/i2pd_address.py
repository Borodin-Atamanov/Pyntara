"""Command: the I2P tunnel address of this machine and its ssh command.

The command decodes the live i2pd keys file of the SSH tunnel and prints
one JSON record: the .b32.i2p address, the sshd port, the local SOCKS
proxy of the router and the ssh command that reaches the SSH daemon
through the tunnel. When the keys file is missing or broken, the saved
address file written by the i2pd_service_setup task is the fallback: the
record then carries a note with the reason, so a collector that keeps
the document keeps the error instead of losing it. When neither source
yields an address, the command exits nonzero with an explanation on
stderr.

The sshd port and the SOCKS proxy port come from the single system config
the command is given, which is the same source the tasks read, so the
report can never name a port the machine does not listen on
(docs/spec/system-metrics.md, section Report collector). Runs as
`python -m pyntara.i2pd_address CONFIG_PATH`.
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
from pyntara.i2pd import b32_address
from pyntara.ssh import ssh_port_from_directives
from pyntara.ssh_access import socks_proxy_address, ssh_command

# The identity may have been recreated between two provisioning runs
# without the task noticing, so the saved address file is the fallback of
# the live keys file.
FALLBACK_NOTE = "address read from the saved file, the keys file is missing or broken"


def resolve_address(keys_path: Path, saved_path: Path) -> tuple[str, str]:
    """The (address, note) of the tunnel.

    The live keys file is the primary source; the saved address file is
    the fallback. An empty address means no source yielded one, and the
    caller reports the failure.
    """

    address = b32_address(keys_path)
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
    """The report record of the I2P channel, or (None, reason).

    Every value of the record comes from the config, so the address, the
    port and the proxy of the command belong to the machine the report
    describes and to the tunnel that is actually published.
    """

    setup = cfg.i2pd_service_setup
    missing = absent_config_keys(
        setup, ("tunnel_keys_path", "address_file_path", "socks_proxy_port")
    )
    if missing:
        return None, (
            "the i2pd_service_setup section of the config has no "
            + ", ".join(missing)
        )
    address, note = resolve_address(setup.tunnel_keys_path, setup.address_file_path)
    if not address:
        return None, "I2P tunnel address is not available"
    try:
        port = ssh_port_from_directives(cfg.ssh_daemon_setup.directives)
    except RuntimeError as exc:
        return None, str(exc)
    engine = cfg.engine
    keys = engine.report_record_keys
    proxy = socks_proxy_address(engine, setup.socks_proxy_port)
    record: dict[str, object] = {
        keys["channel"]: setup.report_channel_name,
        keys["address"]: address,
        keys["port"]: port,
        keys["proxy"]: proxy,
        keys["ssh"]: ssh_command(engine, address, port, proxy),
    }
    if note:
        record[keys["note"]] = note
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
    print(json.dumps(record, ensure_ascii=False, indent=cfg.engine.report_json_indent))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
