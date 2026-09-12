"""Command: the public addresses of this machine with their ssh commands.

The System Metrics collector runs this command as its public_address
network module, so the network report carries the address the machine
appears under from the internet and the ssh command that connects to it.
The addresses come from the shared echo service query of
pyntara.public_address, the same function the three_x_ui_xray_setup task
uses, so the detection exists once and the two callers can never
disagree about what a public address is. Both families are asked in the
same parallel call: a machine behind NAT often has no public IPv4
address but does have a public IPv6 address, and the report shows both.

The service list and its timeouts come from the [three_x_ui_xray_setup]
section of the single system config, never duplicated here, and the
process bound is the collector command timeout, because a module that
outlives it is killed anyway.

A family without an answer contributes a reason record instead of an
address, so the report shows which detection failed instead of dropping
it silently; when neither family answers, the command exits nonzero with
the reason on stderr. Runs as `python -m pyntara.public_address_report
CONFIG_PATH` (docs/spec/system-metrics.md, section Report collector).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pyntara.config import absent_config_keys, load_config
from pyntara.public_address import PublicAddresses, fetch_public_addresses
from pyntara.ssh import ssh_port_from_directives
from pyntara.ssh_access import ssh_command

# The reason a family without an address carries into the report.
NO_ANSWER_REASON = "no echo service reported an address of this family"


def address_records(
    addresses: PublicAddresses, ssh_port: int
) -> list[dict[str, object]]:
    """One record per reported address, plus a reason per silent family."""

    records: list[dict[str, object]] = []
    for family, values in (
        ("ipv4", addresses.ipv4),
        ("ipv6", addresses.ipv6),
    ):
        if not values:
            records.append({"family": family, "reason": NO_ANSWER_REASON})
            continue
        records.extend(
            {
                "address": address,
                "family": family,
                "ssh": ssh_command(address, ssh_port),
            }
            for address in values
        )
    return records


def main(argv: list[str]) -> int:
    """Print the public address records; 0 when found, 2 on a usage error.

    The whole detection failed when no service reported any address: the
    reason goes to stderr with exit code 1, so the collector reports an
    error instead of an empty module.
    """

    if len(argv) != 2:
        print(f"usage: {argv[0]} CONFIG_PATH", file=sys.stderr)
        return 2
    cfg = load_config(Path(argv[1]))
    echo = cfg.three_x_ui_xray_setup
    if not echo.server_ip_services:
        print(
            "error: no echo service is configured in the "
            "three_x_ui_xray_setup section of the config",
            file=sys.stderr,
        )
        return 1
    missing = absent_config_keys(echo, ("server_ip_timeout_seconds",))
    if missing:
        print(
            "error: the three_x_ui_xray_setup section of the config has no "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    try:
        ssh_port = ssh_port_from_directives(cfg.ssh_daemon_setup.directives)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    addresses = fetch_public_addresses(
        cfg.engine,
        echo.server_ip_services,
        echo.server_ip_timeout_seconds,
        cfg.system_metrics_setup.collector.command_timeout_seconds,
    )
    if addresses.is_empty:
        print(
            "error: no echo service reported a public address of this machine",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            address_records(addresses, ssh_port), ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
