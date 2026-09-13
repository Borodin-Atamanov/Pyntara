"""Command: every local address of one family with its ssh access command.

The System Metrics collector runs this command as its ipv4 and ipv6
network modules, so every address the machine carries reaches the
network report and each address carries the ssh command that connects to
it. The addresses come from the configured iproute2 query in JSON form,
which reports every address of every interface together with its
family, scope and interface name: the loopback, link scope, global,
bridge and overlay addresses are all present, so no address class is
lost the way a scope filter loses it.

A link scope IPv6 address is reachable only through the interface that
owns it, so its ssh command carries the zone index (%interface) while
the address field stays the plain address; the scope value that counts
as a link scope is the configured one. A family the machine does not
carry prints nothing and exits 0, so the module reports empty instead of
error: a machine without IPv6 is not a failure.

The ssh command needs the sshd listen port, so the command reads the
single system config it is given, which is the same source the SSH
daemon task writes (architecture contract, Configuration). Runs as
`python -m pyntara.network_addresses CONFIG_PATH FAMILY`, where FAMILY
is one of the flags the engine table maps to a family
(docs/spec/system-metrics.md, section Report collector).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from pyntara.config import Config, EngineConfig, load_config
from pyntara.ssh import ssh_port_from_directives
from pyntara.ssh_access import ssh_command
from pyntara.utils import run_command


@dataclass(frozen=True)
class InterfaceAddress:
    """One address of one interface as iproute2 reports it."""

    address: str
    family: str
    interface: str
    scope: str

    def ssh_target(self, link_scope_name: str) -> str:
        """The address as an ssh target, with the zone of a link address.

        An IPv6 link scope address without its zone index is ambiguous
        (every interface carries one), so the interface name is appended
        after a percent sign, which is the form ssh resolves. The scope
        value counts as a link scope only when it equals the configured
        link_scope_name, so the vocabulary stays in the config.
        """

        if self.family == "ipv6" and self.scope == link_scope_name:
            return f"{self.address}%{self.interface}"
        return self.address


def parse_interface_addresses(
    engine: EngineConfig, document: object, family: str
) -> tuple[InterfaceAddress, ...]:
    """Every address of one family in the document, in the order ip reports.

    The document is the parsed output of the configured iproute2 query. A
    document of an unexpected shape contributes nothing instead of
    raising, so a future iproute2 change is reported as a missing
    address, never as a traceback on the target machine. The family name
    iproute2 prints is the configured one, so a rename of its vocabulary
    is a config change.
    """

    ip_family = engine.iproute2_address_family_names[family]
    addresses: list[InterfaceAddress] = []
    if not isinstance(document, list):
        return ()
    for interface in document:
        if not isinstance(interface, dict):
            continue
        name = interface.get("ifname")
        if not isinstance(name, str) or not name:
            continue
        info_list = interface.get("addr_info")
        if not isinstance(info_list, list):
            continue
        for info in info_list:
            if not isinstance(info, dict) or info.get("family") != ip_family:
                continue
            local = info.get("local")
            if not isinstance(local, str) or not local:
                continue
            scope = info.get("scope")
            addresses.append(
                InterfaceAddress(
                    address=local,
                    family=family,
                    interface=name,
                    scope=scope if isinstance(scope, str) else "",
                )
            )
    return tuple(addresses)


def address_records(
    cfg: Config, document: object, family: str, ssh_port: int
) -> list[dict[str, object]]:
    """The report records of every address of one family.

    The field names and the ssh command of a record come from the config,
    so the shape of the report lives in one place.
    """

    engine = cfg.engine
    keys = engine.report_record_keys
    link_scope_name = engine.link_scope_name
    return [
        {
            keys["address"]: entry.address,
            keys["family"]: entry.family,
            keys["interface"]: entry.interface,
            keys["scope"]: entry.scope,
            keys["ssh"]: ssh_command(
                engine, entry.ssh_target(link_scope_name), ssh_port
            ),
        }
        for entry in parse_interface_addresses(engine, document, family)
    ]


def main(argv: list[str]) -> int:
    """Print the records of one family; 0 when done, 2 on a usage error.

    An unreadable address list is reported on stderr with exit code 1,
    so the collector shows the failure instead of an empty module. A
    family without an address prints nothing and exits 0.
    """

    if len(argv) != 3:
        print(f"usage: {argv[0]} CONFIG_PATH FAMILY", file=sys.stderr)
        return 2
    cfg = load_config(Path(argv[1]))
    if argv[2] not in cfg.engine.address_family_by_flag:
        print(f"usage: {argv[0]} CONFIG_PATH FAMILY", file=sys.stderr)
        return 2
    family = cfg.engine.address_family_by_flag[argv[2]]
    try:
        ssh_port = ssh_port_from_directives(cfg.ssh_daemon_setup.directives)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        result = run_command(
            list(cfg.engine.interface_addresses_command),
            check=False,
            capture=True,
            timeout=cfg.engine.command_timeout_seconds,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"error: cannot read the interface addresses: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(
            f"error: ip exited {result.returncode}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return 1
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"error: cannot read the ip JSON output: {exc}", file=sys.stderr)
        return 1
    records = address_records(cfg, document, family, ssh_port)
    if not records:
        return 0
    print(
        json.dumps(records, ensure_ascii=False, indent=cfg.engine.report_json_indent)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
