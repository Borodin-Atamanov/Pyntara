"""Command: the router rules of this machine, with the scope they reach.

The System Metrics collector runs this command as its upnp network module,
so every network report carries the address and the port the internet
reaches this machine on, together with the ssh command that uses them. The
rules are read live from the router through the UPnP client, which is the
source of truth: no file has to be kept in step with the router, and a rule
that disappeared from the router disappears from the report in the same
moment. Only the rules this machine owns are reported, recognised by the
configured description, so the rules of another program on the same router
stay out of the report.

The scope of the address says how far it reaches. A global address is one
the internet can route to, so the forwarded port is reachable from
anywhere; a router that itself sits behind another NAT reports an address
of the provider network, and that address is reachable only from the
networks that route to it. The scope travels in the record, so a reader of
the report never mistakes a narrow address for a wide one.

A machine without the client, without a router or without a rule of its own
prints nothing and exits 0, so an empty module appears in the report
instead of an error. Runs as
`python -m pyntara.upnp_forwarding_state`.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import sys

from pyntara import upnp
from pyntara.ssh_access import host_from_address, ssh_command
from pyntara.values import engine as engine_values
from pyntara.values import upnp_forwarding_setup as values


def address_scope(address: str, global_name: str, nat_name: str) -> str:
    """The scope name of a router address.

    A global address is one the internet can route to, so the forwarded
    port is reachable from anywhere; anything else (the shared address
    space of a provider NAT, a private address of a router that sits behind
    another router) is reachable only from the networks that route to it. A
    value that is not an address at all takes the narrower scope, because
    claiming a reachability that was not proven is the worse mistake.
    """

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return nat_name
    return global_name if parsed.is_global else nat_name


def mapping_records(
    router_address: str, listing: str
) -> list[dict[str, object]]:
    """One record per rule of this machine in the router mapping list.

    The field names come from the declared report vocabulary, the channel
    name from the declared values of the section, and the ssh command from
    the shared builder, so the record has the same shape as every other
    address the report carries.
    """

    keys = engine_values.REPORT_RECORD_KEYS
    scope = address_scope(
        router_address, values.GLOBAL_SCOPE_NAME, values.NAT_SCOPE_NAME
    )
    ours = upnp.mapping_description(
        values.UPNP_MAPPING_DESCRIPTION, socket.gethostname()
    )
    host = host_from_address(router_address)
    records: list[dict[str, object]] = []
    for mapping in upnp.parse_port_mappings(
        listing,
        engine_values.UPNPC_PROTOCOL_NAMES,
        engine_values.UPNPC_MAPPING_ARROW,
    ):
        if mapping.description != ours:
            continue
        records.append(
            {
                keys["channel"]: values.REPORT_CHANNEL_NAME,
                keys["address"]: router_address,
                keys["port"]: mapping.external_port,
                keys["local_port"]: mapping.internal_port,
                keys["scope"]: scope,
                keys["ssh"]: ssh_command(host, mapping.external_port),
            }
        )
    return records


def main(argv: list[str]) -> int:
    """Print the records of the rules of this machine; nothing when there are none.

    Returns 0 in every case that means "this machine has no forwarded
    address to report": no client, no router and no rule of its own are
    normal states of a network, and an empty module carries that answer.
    Returns 2 on a wrong argument count; the values it reads are declared,
    so no argument and no document take part in the answer.
    """

    if len(argv) != 1:
        print(f"usage: {argv[0]}", file=sys.stderr)
        return 2
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    command = values.UPNP_CLIENT_COMMAND
    # The document on stdout is what the collector keeps as records, so the
    # client runs without its command echo: a progress line would turn the
    # document into text and the report would lose the fields.
    router_address = upnp.router_external_address(command, timeout, log_command=False)
    if router_address is None:
        return 0
    records = mapping_records(
        router_address,
        upnp.list_mappings(command, timeout, log_command=False),
    )
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
