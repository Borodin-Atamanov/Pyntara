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
4. the machine's own subnets and the private ranges go directly;
5. on a machine in Russia the resources that are reachable only from
   inside Russia go directly, the services that refuse to serve Russia
   and the resources blocked in Russia go through the remote server, and
   everything else goes directly;
6. anywhere else everything else goes through the remote server.

A machine that is the remote server itself has no remote outbound, so the
rules that point at it are not built at all: nothing connects to itself.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass

# The panel accepts a fixed set of inbound protocols and has no plain
# "socks" inbound: "mixed" serves SOCKS5 and HTTP on one port, which is
# what a local proxy needs.
PANEL_MIXED_PROTOCOL = "mixed"

# The rules the panel ships by default that make the proxy less useful
# than it can be: bittorrent traffic is blocked, every private destination
# is blocked instead of going directly, and the direct outbound carries
# internal block rules of its own. The policy removes them, so the proxy
# passes what the machine can reach.
PANEL_BLOCKED_PROTOCOLS = ("bittorrent",)
PANEL_PRIVATE_BLOCK_CATEGORY = "geoip:private"


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


def parse_vless_link(link: str) -> VlessProfile | None:
    """The profile carried by a vless share link, or None.

    None means the value cannot produce a working outbound: an empty
    value, another scheme, a missing address or client id, or a REALITY
    link without a public key. A caller reports that as a warning and
    configures no remote outbound instead of writing a broken one.
    """

    candidate = link.strip()
    if not candidate:
        return None
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme != "vless":
        return None
    address = parsed.hostname or ""
    client_id = urllib.parse.unquote(parsed.username or "")
    if not address or not client_id:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    security = (query.get("security") or ["none"])[0]
    public_key = (query.get("pbk") or [""])[0]
    if security == "reality" and not public_key:
        return None
    return VlessProfile(
        address=address,
        port=parsed.port or 443,
        client_id=client_id,
        security=security,
        fingerprint=(query.get("fp") or [""])[0],
        public_key=public_key,
        short_id=(query.get("sid") or [""])[0],
        server_name=(query.get("sni") or [""])[0],
        spider_x=urllib.parse.unquote((query.get("spx") or [""])[0]),
        flow=(query.get("flow") or [""])[0],
        network=(query.get("type") or ["tcp"])[0],
    )


def build_remote_outbound(tag: str, profile: VlessProfile) -> dict[str, object]:
    """The vless REALITY outbound that carries traffic to the remote server."""

    stream: dict[str, object] = {
        "network": profile.network,
        "security": profile.security,
    }
    if profile.security == "reality":
        stream["realitySettings"] = {
            "serverName": profile.server_name,
            "fingerprint": profile.fingerprint,
            "publicKey": profile.public_key,
            "shortId": profile.short_id,
            "spiderX": profile.spider_x,
        }
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": profile.address,
                    "port": profile.port,
                    "users": [
                        {
                            "id": profile.client_id,
                            "encryption": "none",
                            "flow": profile.flow,
                        }
                    ],
                }
            ]
        },
        "streamSettings": stream,
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


def _proxy_outbound(tag: str, protocol: str, address: str) -> dict[str, object]:
    """An outbound that hands traffic to a local proxy of this machine."""

    host, port = _split_host_port(address)
    return {
        "tag": tag,
        "protocol": protocol,
        "settings": {"servers": [{"address": host, "port": port}]},
    }


def build_tor_outbound(tag: str, address: str) -> dict[str, object]:
    """The outbound that hands .onion traffic to the local tor SOCKS proxy."""

    return _proxy_outbound(tag, "socks", address)


def build_i2p_outbound(tag: str, address: str) -> dict[str, object]:
    """The outbound that hands .i2p traffic to the local i2pd HTTP proxy."""

    return _proxy_outbound(tag, "http", address)


def build_local_proxy_inbound(
    *,
    tag: str,
    remark: str,
    listen_address: str,
    port: int,
    udp_enabled: bool,
    sniffing_protocols: tuple[str, ...],
) -> dict[str, object]:
    """The panel payload for the local proxy inbound.

    The panel has no plain "socks" inbound protocol: "mixed" serves SOCKS5
    and HTTP on one port, which is exactly a local proxy. The inbound is
    created without a traffic limit and without an expiry date, because a
    local proxy that stops working after a quota is worse than useless.
    Sniffing is what lets the rules decide by the requested name, so the
    protocols the panel can sniff are enabled.
    """

    return {
        "remark": remark,
        "listen": listen_address,
        "port": port,
        "protocol": PANEL_MIXED_PROTOCOL,
        "tag": tag,
        "enable": True,
        "expiryTime": 0,
        "total": 0,
        "up": 0,
        "down": 0,
        "settings": {"auth": "noauth", "udp": udp_enabled, "ip": listen_address},
        "sniffing": {
            "enabled": True,
            "destOverride": list(sniffing_protocols),
            "metadataOnly": False,
            "routeOnly": False,
        },
    }


def _field_rule(inbound_tag: str, outbound_tag: str, **criteria: object) -> dict[str, object]:
    """One field rule scoped to the local proxy inbound."""

    return {
        "type": "field",
        "inboundTag": [inbound_tag],
        **criteria,
        "outboundTag": outbound_tag,
    }


def build_routing_rules(
    policy: LocalProxyPolicy, *, remote_outbound_available: bool
) -> list[dict[str, object]]:
    """The rules the policy owns, in their fixed order.

    With remote_outbound_available=False the machine is the remote server
    itself: the rules that would send traffic to it are left out, so
    nothing connects to its own address and no rule points at an outbound
    that does not exist.
    """

    rules: list[dict[str, object]] = []
    if policy.ad_block_domain_categories:
        rules.append(
            _field_rule(
                policy.inbound_tag,
                policy.blocked_outbound_tag,
                domain=list(policy.ad_block_domain_categories),
            )
        )
    rules.append(
        _field_rule(
            policy.inbound_tag, policy.tor_outbound_tag, domain=["domain:.onion"]
        )
    )
    rules.append(
        _field_rule(
            policy.inbound_tag, policy.i2p_outbound_tag, domain=["domain:.i2p"]
        )
    )
    if policy.direct_domains:
        rules.append(
            _field_rule(
                policy.inbound_tag, policy.direct_outbound_tag, domain=list(policy.direct_domains)
            )
        )
    direct_ips = policy.direct_ip_values()
    if direct_ips:
        rules.append(
            _field_rule(
                policy.inbound_tag, policy.direct_outbound_tag, ip=direct_ips
            )
        )
    if not remote_outbound_available:
        return rules
    if policy.in_russia:
        if policy.russia_direct_domain_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.direct_outbound_tag,
                    domain=list(policy.russia_direct_domain_categories),
                )
            )
        if policy.russia_direct_ip_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.direct_outbound_tag,
                    ip=list(policy.russia_direct_ip_categories),
                )
            )
        if policy.geo_restricted_domain_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.remote_outbound_tag,
                    domain=list(policy.geo_restricted_domain_categories),
                )
            )
        if policy.russia_blocked_domain_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.remote_outbound_tag,
                    domain=list(policy.russia_blocked_domain_categories),
                )
            )
        if policy.russia_blocked_ip_categories:
            rules.append(
                _field_rule(
                    policy.inbound_tag,
                    policy.remote_outbound_tag,
                    ip=list(policy.russia_blocked_ip_categories),
                )
            )
        rules.append(
            _field_rule(policy.inbound_tag, policy.direct_outbound_tag)
        )
        return rules
    rules.append(_field_rule(policy.inbound_tag, policy.remote_outbound_tag))
    return rules


def _is_own_rule(rule: object, policy: LocalProxyPolicy) -> bool:
    """True when the rule was written by this policy.

    Ownership is decided by the inbound tag alone: every rule of the
    policy is scoped to the local proxy inbound, and no foreign rule
    is.
    """

    if not isinstance(rule, dict):
        return False
    tags = rule.get("inboundTag")
    return isinstance(tags, list) and policy.inbound_tag in tags


def _is_panel_restriction(rule: object, policy: LocalProxyPolicy) -> bool:
    """True for the panel rules that block traffic the proxy should pass."""

    if not isinstance(rule, dict):
        return False
    protocol = rule.get("protocol")
    if isinstance(protocol, list) and set(PANEL_BLOCKED_PROTOCOLS) & set(protocol):
        return True
    ip_entries = rule.get("ip")
    return (
        isinstance(ip_entries, list)
        and PANEL_PRIVATE_BLOCK_CATEGORY in ip_entries
        and rule.get("outboundTag") == policy.blocked_outbound_tag
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
    outbounds = updated.get("outbounds")
    if not isinstance(outbounds, list):
        outbounds = []
    wanted_tags = _own_outbound_tags(policy)
    kept_outbounds = [
        outbound
        for outbound in outbounds
        if not (isinstance(outbound, dict) and outbound.get("tag") in wanted_tags)
    ]
    if remove_panel_restrictions:
        for outbound in kept_outbounds:
            if isinstance(outbound, dict) and outbound.get("tag") == policy.direct_outbound_tag:
                settings = outbound.get("settings")
                if isinstance(settings, dict) and "finalRules" in settings:
                    settings.pop("finalRules", None)
    own_outbounds: list[dict[str, object]] = []
    if remote_outbound is not None:
        own_outbounds.append(remote_outbound)
    own_outbounds.append(build_tor_outbound(policy.tor_outbound_tag, policy.tor_proxy_address))
    own_outbounds.append(build_i2p_outbound(policy.i2p_outbound_tag, policy.i2p_proxy_address))
    updated["outbounds"] = [*kept_outbounds, *own_outbounds]

    routing = updated.get("routing")
    if not isinstance(routing, dict):
        routing = {}
        updated["routing"] = routing
    rules = routing.get("rules")
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
            if isinstance(rule, dict) and rule.get("inboundTag") == ["api"]
        ),
        None,
    )
    rest = [rule for rule in kept_rules if rule is not api_rule]
    our_rules = build_routing_rules(
        policy, remote_outbound_available=remote_outbound is not None
    )
    routing["rules"] = ([api_rule] if api_rule is not None else []) + our_rules + rest
    routing["domainStrategy"] = policy.domain_strategy()

    changed = json.dumps(updated, sort_keys=True) != json.dumps(template, sort_keys=True)
    return updated, changed
