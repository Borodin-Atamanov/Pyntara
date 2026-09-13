"""Builders for the routing policy of the local proxy.

The module turns the configured values into the three parts of the Xray
configuration that make a machine a client of the remote server: the
local proxy inbound, the outbounds (remote vless, tor, i2p) and the
routing rules that decide which outbound a connection takes
(docs/spec/3x-ui.md). Everything here is pure data work: no command runs
and no file is written, so the whole policy is testable without a
machine.

The rule order is fixed and every rule is scoped to the tag of the local
proxy inbound, so traffic that enters the machine any other way (the
server inbound of a panel, for example) keeps its own routing:

1. advertising domains go to the blocked outbound;
2. .onion goes to the tor outbound, .i2p to the i2p outbound;
3. local names go directly, they cannot be resolved by a remote server;
4. on a machine in Russia the names that are reachable only from inside
   Russia go directly, and the names that refuse to serve Russia and the
   names blocked in Russia go through the remote server;
5. the private ranges, the overlay networks and the machine's own subnets
   go directly, and on a machine in Russia the Russian ranges go directly
   while the ranges blocked in Russia go through the remote server;
6. everything else goes through the remote server, or directly on a
   machine in Russia.

Every name rule comes before the first address rule, and that order is
the point rather than a detail. The routing domain strategy of a machine
in Russia is IPIfNonMatch, and the core resolves a name as soon as it
reaches the first rule that carries addresses, in order to match them.
A name that a name list already carries must therefore be decided before
that happens: otherwise the address of the name decides instead, and the
address of a site that is blocked in Russia can fall into a range that is
routed directly, which sends a site that must use the remote server
directly into the block. Measured on a machine in Russia: the domain
api.openai.com was routed to the remote server while the address this
machine resolves for it was routed directly, and the site broke whenever
the address decided.

A machine that is the remote server itself has no remote outbound, so the
rules that point at it are not built at all: nothing connects to itself.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class VlessProfile:
    """The fields of a vless share link that a client outbound needs.

    The link is the single source of truth for the profile: it carries the
    REALITY public key, the short id and the spider path that the server
    entry of the vault repeats only partly.
    """

    address: str
    port: int
    client_id: str
    security: str
    fingerprint: str
    public_key: str
    short_id: str
    server_name: str
    spider_x: str
    flow: str
    network: str


@dataclass(frozen=True)
class LocalProxyPolicy:
    """Everything the inbound, the outbounds and the rules are built from.

    The tags name the objects the policy owns, so it can replace its own
    outbounds and rules on a rerun and leave every foreign object alone.
    The category lists hold ready routing tokens (geosite:, geoip:, ext-):
    the community datasets behind them are maintained outside this
    project, the policy only decides which of them apply.
    panel_inbound_protocol, panel_blocked_rule_protocols and
    panel_private_block_category are the vocabulary of the panel itself,
    so which inbound it accepts and which of its shipped rules count as a
    restriction are config values. field_keys are the names of the fields
    of the Xray document and values the protocol words written into it,
    both from the config, so a core version that renames a field is
    answered there and not here.
    """

    inbound_tag: str
    remote_outbound_tag: str
    tor_outbound_tag: str
    i2p_outbound_tag: str
    direct_outbound_tag: str
    blocked_outbound_tag: str
    tor_proxy_address: str
    i2p_proxy_address: str
    ad_block_domain_categories: tuple[str, ...]
    direct_domains: tuple[str, ...]
    direct_ip_categories: tuple[str, ...]
    direct_ip_networks: tuple[str, ...]
    own_networks: tuple[str, ...]
    in_russia: bool
    russia_blocked_domain_categories: tuple[str, ...]
    russia_blocked_ip_categories: tuple[str, ...]
    russia_direct_domain_categories: tuple[str, ...]
    russia_direct_ip_categories: tuple[str, ...]
    geo_restricted_domain_categories: tuple[str, ...]
    russia_domain_strategy: str
    outside_russia_domain_strategy: str
    panel_inbound_protocol: str
    panel_blocked_rule_protocols: tuple[str, ...]
    panel_private_block_category: str
    field_keys: dict[str, str]
    values: dict[str, str]

    def direct_ip_values(self) -> list[str]:
        """The category and network entries of the direct address rule.

        The private range category comes first, then the configured
        networks (the yggdrasil overlay among them), then the subnets the
        kernel reports, so the rule reads as the machine's own reach in
        the order it was built.
        """

        values: list[str] = []
        for value in (
            *self.direct_ip_categories,
            *self.direct_ip_networks,
            *self.own_networks,
        ):
            if value and value not in values:
                values.append(value)
        return values

    def domain_strategy(self) -> str:
        """The routing domain strategy that fits the current country.

        A machine in Russia matches the address lists of the community
        datasets, which requires resolving a name before the decision can
        be taken; anywhere else no address list decides anything, so the
        cheaper strategy stays.
        """

        return self.russia_domain_strategy if self.in_russia else self.outside_russia_domain_strategy


def parse_vless_link(
    link: str,
    default_port: int,
    scheme: str,
    query_keys: dict[str, str],
    values: dict[str, str],
) -> VlessProfile | None:
    """The profile carried by a share link of the vless scheme, or None.

    None means the value cannot produce a working outbound: an empty
    value, another scheme, a missing address or client id, or a REALITY
    link without a public key. A caller reports that as a warning and
    configures no remote outbound instead of writing a broken one.
    default_port is the configured port of a link that carries none,
    scheme the configured name of the scheme these links use and
    query_keys the configured names of their query parameters, so a panel
    that renames one is answered in the config.
    """

    candidate = link.strip()
    if not candidate:
        return None
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme != scheme:
        return None
    address = parsed.hostname or ""
    client_id = urllib.parse.unquote(parsed.username or "")
    if not address or not client_id:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    security = (query.get(query_keys["security"]) or [values["none"]])[0]
    public_key = (query.get(query_keys["public_key"]) or [""])[0]
    if security == values["reality"] and not public_key:
        return None
    return VlessProfile(
        address=address,
        port=parsed.port or default_port,
        client_id=client_id,
        security=security,
        fingerprint=(query.get(query_keys["fingerprint"]) or [""])[0],
        public_key=public_key,
        short_id=(query.get(query_keys["short_id"]) or [""])[0],
        server_name=(query.get(query_keys["server_name"]) or [""])[0],
        spider_x=urllib.parse.unquote(
            (query.get(query_keys["spider_x"]) or [""])[0]
        ),
        flow=(query.get(query_keys["flow"]) or [""])[0],
        network=(query.get(query_keys["network"]) or [values["tcp"]])[0],
    )


def build_remote_outbound(
    tag: str, profile: VlessProfile, fields: dict[str, str], values: dict[str, str]
) -> dict[str, object]:
    """The vless REALITY outbound that carries traffic to the remote server."""

    stream: dict[str, object] = {
        fields["network"]: profile.network,
        fields["security"]: profile.security,
    }
    if profile.security == values["reality"]:
        stream[fields["reality_settings"]] = {
            fields["server_name"]: profile.server_name,
            fields["fingerprint"]: profile.fingerprint,
            fields["public_key"]: profile.public_key,
            fields["short_id"]: profile.short_id,
            fields["spider_x"]: profile.spider_x,
        }
    return {
        fields["tag"]: tag,
        fields["protocol"]: values["vless"],
        fields["settings"]: {
            fields["vnext"]: [
                {
                    fields["address"]: profile.address,
                    fields["port"]: profile.port,
                    fields["users"]: [
                        {
                            fields["id"]: profile.client_id,
                            fields["encryption"]: values["none"],
                            fields["flow"]: profile.flow,
                        }
                    ],
                }
            ]
        },
        fields["stream_settings"]: stream,
    }


def _split_host_port(address: str) -> tuple[str, int]:
    """The host and the port of an "address:port" value.

    Raises ValueError when the value is not a host with a numeric port, so
    a malformed config entry is reported instead of producing an outbound
    that points nowhere.
    """

    host, separator, port_text = address.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise ValueError(f"expected an address:port value, got {address!r}")
    return host, int(port_text)


def _proxy_outbound(
    tag: str, protocol: str, address: str, fields: dict[str, str]
) -> dict[str, object]:
    """An outbound that hands traffic to a local proxy of this machine."""

    host, port = _split_host_port(address)
    return {
        fields["tag"]: tag,
        fields["protocol"]: protocol,
        fields["settings"]: {
            fields["servers"]: [
                {fields["address"]: host, fields["port"]: port}
            ]
        },
    }


def build_tor_outbound(
    tag: str, address: str, fields: dict[str, str], values: dict[str, str]
) -> dict[str, object]:
    """The outbound that hands .onion traffic to the local tor SOCKS proxy."""

    return _proxy_outbound(tag, values["socks"], address, fields)


def build_i2p_outbound(
    tag: str, address: str, fields: dict[str, str], values: dict[str, str]
) -> dict[str, object]:
    """The outbound that hands .i2p traffic to the local i2pd HTTP proxy."""

    return _proxy_outbound(tag, values["http"], address, fields)


def build_local_proxy_inbound(
    *,
    tag: str,
    protocol: str,
    remark: str,
    listen_address: str,
    port: int,
    udp_enabled: bool,
    enabled: bool,
    traffic_limit_bytes: int,
    expiry_time: int,
    sniffing_enabled: bool,
    sniffing_metadata_only: bool,
    sniffing_route_only: bool,
    sniffing_protocols: tuple[str, ...],
    fields: dict[str, str],
    values: dict[str, str],
) -> dict[str, object]:
    """The panel payload for the local proxy inbound.

    protocol is the configured inbound protocol of the panel, mixed by
    default: the panel has no plain "socks" inbound, and mixed serves
    SOCKS5 and HTTP on one port, which is exactly a local proxy. The
    inbound is created with the configured traffic limit and expiry date,
    both of which ship as zero, because a local proxy that stops working
    after a quota is worse than useless. Sniffing is what lets the rules
    decide by the requested name, so the protocols the panel can sniff are
    enabled, and how the panel treats the sniffed result is configured as
    well. The field names come from the config, so a panel or core version
    that renames one is answered there.
    """

    return {
        fields["remark"]: remark,
        fields["listen"]: listen_address,
        fields["port"]: port,
        fields["protocol"]: protocol,
        fields["tag"]: tag,
        fields["enable"]: enabled,
        fields["expiry_time"]: expiry_time,
        fields["total"]: traffic_limit_bytes,
        fields["up"]: 0,
        fields["down"]: 0,
        fields["settings"]: {
            fields["auth"]: values["noauth"],
            fields["udp"]: udp_enabled,
            fields["ip"]: listen_address,
        },
        fields["sniffing"]: {
            fields["enabled"]: sniffing_enabled,
            fields["dest_override"]: list(sniffing_protocols),
            fields["metadata_only"]: sniffing_metadata_only,
            fields["route_only"]: sniffing_route_only,
        },
    }


def _field_rule(
    inbound_tag: str,
    outbound_tag: str,
    fields: dict[str, str],
    values: dict[str, str],
    **criteria: object,
) -> dict[str, object]:
    """One field rule scoped to the local proxy inbound."""

    return {
        fields["type"]: values["field"],
        fields["inbound_tag"]: [inbound_tag],
        **criteria,
        fields["outbound_tag"]: outbound_tag,
    }


def build_routing_rules(
    policy: LocalProxyPolicy, *, remote_outbound_available: bool
) -> list[dict[str, object]]:
    """The rules the policy owns, in their fixed order.

    Every name rule is built before the first address rule. The core
    resolves a name at the first rule that carries addresses when the
    strategy is IPIfNonMatch, so a name that a name list carries has to be
    decided by name first; otherwise the address decides, and the address
    of a site blocked in Russia can fall into a range that goes directly.
    The module docstring records the measurement behind this order.

    With remote_outbound_available=False the machine is the remote server
    itself: the rules that would send traffic to it are left out, so
    nothing connects to its own address and no rule points at an outbound
    that does not exist.
    """

    rules: list[dict[str, object]] = []
    fields = policy.field_keys
    values = policy.values
    if policy.ad_block_domain_categories:
        rules.append(
            _field_rule(
                policy.inbound_tag,
                policy.blocked_outbound_tag,
                fields,
                values,
                **{fields["domain"]: list(policy.ad_block_domain_categories)},
            )
        )
    rules.append(
        _field_rule(
            policy.inbound_tag,
            policy.tor_outbound_tag,
            fields,
            values,
            **{fields["domain"]: [values["onion_domain"]]},
        )
    )
    rules.append(
        _field_rule(
            policy.inbound_tag,
            policy.i2p_outbound_tag,
            fields,
            values,
            **{fields["domain"]: [values["i2p_domain"]]},
        )
    )
    if policy.direct_domains:
        rules.append(
            _field_rule(
                policy.inbound_tag,
                policy.direct_outbound_tag,
                fields,
                values,
                **{fields["domain"]: list(policy.direct_domains)},
            )
        )
    if remote_outbound_available and policy.in_russia:
        if policy.russia_direct_domain_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.direct_outbound_tag,
                    fields,
                    values,
                    **{
                        fields["domain"]: list(
                            policy.russia_direct_domain_categories
                        )
                    },
                )
            )
        if policy.geo_restricted_domain_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.remote_outbound_tag,
                    fields,
                    values,
                    **{
                        fields["domain"]: list(
                            policy.geo_restricted_domain_categories
                        )
                    },
                )
            )
        if policy.russia_blocked_domain_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.remote_outbound_tag,
                    fields,
                    values,
                    **{
                        fields["domain"]: list(
                            policy.russia_blocked_domain_categories
                        )
                    },
                )
            )
    direct_ips = policy.direct_ip_values()
    if direct_ips:
        rules.append(
            _field_rule(
                policy.inbound_tag,
                policy.direct_outbound_tag,
                fields,
                values,
                **{fields["ip"]: direct_ips},
            )
        )
    if not remote_outbound_available:
        return rules
    if policy.in_russia:
        if policy.russia_direct_ip_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.direct_outbound_tag,
                    fields,
                    values,
                    **{
                        fields["ip"]: list(
                            policy.russia_direct_ip_categories
                        )
                    },
                )
            )
        if policy.russia_blocked_ip_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.remote_outbound_tag,
                    fields,
                    values,
                    **{
                        fields["ip"]: list(
                            policy.russia_blocked_ip_categories
                        )
                    },
                )
            )
        rules.append(
            _field_rule(
                policy.inbound_tag,
                policy.direct_outbound_tag,
                fields,
                values,
            )
        )
        return rules
    rules.append(
        _field_rule(
            policy.inbound_tag,
            policy.remote_outbound_tag,
            fields,
            values,
        )
    )
    return rules


def _is_own_rule(rule: object, policy: LocalProxyPolicy) -> bool:
    """True when the rule was written by this policy.

    Ownership is decided by the inbound tag alone: every rule of the
    policy is scoped to the local proxy inbound, and no foreign rule
    is.
    """

    if not isinstance(rule, dict):
        return False
    tags = rule.get(policy.field_keys["inbound_tag"])
    return isinstance(tags, list) and policy.inbound_tag in tags


def _is_panel_restriction(rule: object, policy: LocalProxyPolicy) -> bool:
    """True for the panel rules that block traffic the proxy should pass."""

    if not isinstance(rule, dict):
        return False
    protocol = rule.get(policy.field_keys["protocol"])
    if isinstance(protocol, list) and set(
        policy.panel_blocked_rule_protocols
    ) & set(protocol):
        return True
    ip_entries = rule.get(policy.field_keys["ip"])
    return (
        isinstance(ip_entries, list)
        and policy.panel_private_block_category in ip_entries
        and rule.get(policy.field_keys["outbound_tag"])
        == policy.blocked_outbound_tag
    )


def _own_outbound_tags(policy: LocalProxyPolicy) -> set[str]:
    """The outbound tags the policy owns and replaces on every run."""

    return {
        policy.remote_outbound_tag,
        policy.tor_outbound_tag,
        policy.i2p_outbound_tag,
    }


def apply_routing_policy(
    template: dict[str, object],
    policy: LocalProxyPolicy,
    *,
    remote_outbound: dict[str, object] | None,
    remove_panel_restrictions: bool,
) -> tuple[dict[str, object], bool]:
    """Bring the Xray template to the wanted state; report whether it changed.

    Only the policy's own outbounds, its own rules and the recorded panel
    restrictions are touched: every other outbound, rule and section of
    the template is kept as it is, so a panel that grows new settings or
    an operator who edits the template by hand never loses work. The
    function is pure, so a caller compares the result with the template it
    read and writes only when something really differs.
    """

    updated = json.loads(json.dumps(template))
    fields = policy.field_keys
    outbounds = updated.get(fields["outbounds"])
    if not isinstance(outbounds, list):
        outbounds = []
    wanted_tags = _own_outbound_tags(policy)
    kept_outbounds = [
        outbound
        for outbound in outbounds
        if not (
            isinstance(outbound, dict)
            and outbound.get(fields["tag"]) in wanted_tags
        )
    ]
    if remove_panel_restrictions:
        for outbound in kept_outbounds:
            if (
                isinstance(outbound, dict)
                and outbound.get(fields["tag"]) == policy.direct_outbound_tag
            ):
                settings = outbound.get(fields["settings"])
                if (
                    isinstance(settings, dict)
                    and fields["final_rules"] in settings
                ):
                    settings.pop(fields["final_rules"], None)
    own_outbounds: list[dict[str, object]] = []
    if remote_outbound is not None:
        own_outbounds.append(remote_outbound)
    own_outbounds.append(
        build_tor_outbound(
            policy.tor_outbound_tag,
            policy.tor_proxy_address,
            fields,
            policy.values,
        )
    )
    own_outbounds.append(
        build_i2p_outbound(
            policy.i2p_outbound_tag,
            policy.i2p_proxy_address,
            fields,
            policy.values,
        )
    )
    updated[fields["outbounds"]] = [*kept_outbounds, *own_outbounds]

    routing = updated.get(fields["routing"])
    if not isinstance(routing, dict):
        routing = {}
        updated[fields["routing"]] = routing
    rules = routing.get(fields["rules"])
    if not isinstance(rules, list):
        rules = []
    kept_rules = [rule for rule in rules if not _is_own_rule(rule, policy)]
    if remove_panel_restrictions:
        kept_rules = [
            rule for rule in kept_rules if not _is_panel_restriction(rule, policy)
        ]
    api_rule = next(
        (
            rule
            for rule in kept_rules
            if isinstance(rule, dict)
            and rule.get(fields["inbound_tag"])
            == [policy.values["api_tag"]]
        ),
        None,
    )
    rest = [rule for rule in kept_rules if rule is not api_rule]
    our_rules = build_routing_rules(
        policy, remote_outbound_available=remote_outbound is not None
    )
    routing[fields["rules"]] = (
        ([api_rule] if api_rule is not None else []) + our_rules + rest
    )
    routing[fields["domain_strategy"]] = policy.domain_strategy()

    changed = json.dumps(updated, sort_keys=True) != json.dumps(template, sort_keys=True)
    return updated, changed
