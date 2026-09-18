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
is one of the flags ADDRESS_FAMILY_BY_FLAG maps to a family
(docs/spec/system-metrics.md, section Report collector).
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import sys
from dataclasses import dataclass

from pyntara.ssh import ssh_port_from_directives
from pyntara.ssh_access import ssh_command
from pyntara.utils import run_command
from pyntara.values import engine as engine_values


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
        after a percent sign, which is the form ssh resolves. The address
        itself decides this, because the version of the address is a fact
        and the names the declared mapping gives the families are a
        vocabulary; the scope value counts as a link scope only when it
        equals the declared LINK_SCOPE_NAME.
        """

        if self.scope == link_scope_name and _is_ipv6(self.address):
            return f"{self.address}%{self.interface}"
        return self.address


def _is_ipv6(address: str) -> bool:
    """Whether the address text is an IPv6 address, judged by its own form."""

    try:
        return ipaddress.ip_address(address).version == 6
    except ValueError:
        return False


def parse_interface_addresses(
    document: object, family: str
) -> tuple[InterfaceAddress, ...]:
    """Every address of one family in the document, in the order ip reports.

    The document is the parsed output of the declared iproute2 query. A
    document of an unexpected shape contributes nothing instead of
    raising, so a future iproute2 change is reported as a missing
    address, never as a traceback on the target machine. The family name
    iproute2 prints is the declared one, so a rename of its vocabulary is
    a change of a declared value.
    """

    ip_family = engine_values.IPROUTE2_ADDRESS_FAMILY_NAMES[family]
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
    document: object, family: str, ssh_port: int
) -> list[dict[str, object]]:
    """The report records of every address of one family.

    The field names and the ssh command of a record come from the declared
    values, so the shape of the report lives in one place.
    """

    keys = engine_values.REPORT_RECORD_KEYS
    link_scope_name = engine_values.LINK_SCOPE_NAME
    return [
        {
            keys["address"]: entry.address,
            keys["family"]: entry.family,
            keys["interface"]: entry.interface,
            keys["scope"]: entry.scope,
            keys["ssh"]: ssh_command(entry.ssh_target(link_scope_name), ssh_port),
        }
        for entry in parse_interface_addresses(document, family)
    ]


def main(argv: list[str]) -> int:
    """Print the records of one family; 0 when done, 2 on a usage error.

    An unreadable address list is reported on stderr with exit code 1,
    so the collector shows the failure instead of an empty module. A
    family without an address prints nothing and exits 0. The sshd port
    comes from the declared ssh_daemon_setup directives, so the command
    needs no argument beyond the family.
    """

    if len(argv) != 2:
        print(f"usage: {argv[0]} FAMILY", file=sys.stderr)
        return 2
    if argv[1] not in engine_values.ADDRESS_FAMILY_BY_FLAG:
        print(f"usage: {argv[0]} FAMILY", file=sys.stderr)
        return 2
    family = engine_values.ADDRESS_FAMILY_BY_FLAG[argv[1]]
    try:
        ssh_port = ssh_port_from_directives()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        result = run_command(
            list(engine_values.INTERFACE_ADDRESSES_COMMAND),
            check=False,
            capture=True,
            timeout=engine_values.COMMAND_TIMEOUT_SECONDS,
            log_command=False,
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
    records = address_records(document, family, ssh_port)
    if not records:
        return 0
    print(
        json.dumps(
            records,
            ensure_ascii=False,
            indent=engine_values.REPORT_JSON_INDENT,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
