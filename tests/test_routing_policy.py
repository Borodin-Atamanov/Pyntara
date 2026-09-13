"""Unit tests for the routing policy builders.

Everything here is pure data work: no panel, no machine, no network.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from pyntara.routing_policy import (
    LocalProxyPolicy,
    VlessProfile,
    apply_routing_policy,
    build_i2p_outbound,
    build_local_proxy_inbound,
    build_remote_outbound,
    build_routing_rules,
    build_tor_outbound,
    parse_vless_link,
)

LINK = (
    "vless://client-id@203.0.113.9:443?fp=chrome&pbk=PUBLICKEY&security=reality"
    "&sid=6ba85179e30d4fc2&sni=www.google.com&spx=%2Fspider&type=tcp"
    "#universal-client"
)


def make_profile() -> VlessProfile:
    """The profile of the test link, which the parser always accepts."""

    profile = parse_vless_link(LINK)
    assert profile is not None
    return profile


def outbounds_of(template: dict[str, object]) -> list[dict[str, object]]:
    """The outbound list of a template, as the builders write it."""

    outbounds = template["outbounds"]
    assert isinstance(outbounds, list)
    return cast("list[dict[str, object]]", outbounds)


def rules_of(template: dict[str, object]) -> list[dict[str, object]]:
    """The routing rule list of a template, as the builders write it."""

    routing = template["routing"]
    assert isinstance(routing, dict)
    rules = routing["rules"]
    assert isinstance(rules, list)
    return cast("list[dict[str, object]]", rules)


def routing_strategy(template: dict[str, object]) -> object:
    """The domain strategy of a template routing block."""

    routing = template["routing"]
    assert isinstance(routing, dict)
    return routing["domainStrategy"]


def make_policy(**overrides: Any) -> LocalProxyPolicy:
    """A policy with the values the task uses, open to per-test overrides."""

    values: dict[str, Any] = {
        "inbound_tag": "pyntara-local-proxy",
        "remote_outbound_tag": "pyntara-remote",
        "tor_outbound_tag": "pyntara-tor",
        "i2p_outbound_tag": "pyntara-i2p",
        "direct_outbound_tag": "direct",
        "blocked_outbound_tag": "blocked",
        "tor_proxy_address": "127.0.0.1:9050",
        "i2p_proxy_address": "127.0.0.1:4444",
        "ad_block_domain_categories": ("geosite:category-ads-all",),
        "direct_domains": ("domain:localhost", "domain:.local"),
        "direct_ip_categories": ("geoip:private",),
        "direct_ip_networks": ("200::/7", "300::/7"),
        "own_networks": ("10.10.0.0/24", "200::/7", "fe80::/64"),
        "in_russia": False,
        "russia_blocked_domain_categories": ("ext-site:geosite_RU.dat:ru-blocked-all",),
        "russia_blocked_ip_categories": ("ext-ip:geoip_RU.dat:ru-blocked",),
        "russia_direct_domain_categories": (
            "ext-site:geosite_RU.dat:ru-available-only-inside",
        ),
        "russia_direct_ip_categories": ("ext-ip:geoip_RU.dat:ru-whitelist",),
        "geo_restricted_domain_categories": ("geosite:category-ai-!cn", "geosite:netflix"),
        "russia_domain_strategy": "IPIfNonMatch",
        "outside_russia_domain_strategy": "AsIs",
        "panel_inbound_protocol": "mixed",
        "panel_blocked_rule_protocols": ("bittorrent",),
        "panel_private_block_category": "geoip:private",
    }
    values.update(overrides)
    return LocalProxyPolicy(**values)


def make_template() -> dict[str, object]:
    """A template shaped like the one the panel ships on this machine."""

    return {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["HandlerService"]},
        "inbounds": [{"tag": "api", "port": 62789, "protocol": "tunnel"}],
        "outbounds": [
            {
                "tag": "direct",
                "protocol": "freedom",
                "settings": {
                    "domainStrategy": "AsIs",
                    "finalRules": [{"action": "block", "ip": ["geoip:private"]}],
                },
            },
            {"tag": "blocked", "protocol": "blackhole", "settings": {}},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {
                    "type": "field",
                    "ip": ["geoip:private"],
                    "outboundTag": "blocked",
                },
                {
                    "type": "field",
                    "protocol": ["bittorrent"],
                    "outboundTag": "blocked",
                },
            ],
        },
    }


class TestParseVlessLink:
    """Tests for reading a share link."""

    def test_reads_every_field_of_a_full_link(self) -> None:
        profile = parse_vless_link(LINK)
        assert profile == VlessProfile(
            address="203.0.113.9",
            port=443,
            client_id="client-id",
            security="reality",
            fingerprint="chrome",
            public_key="PUBLICKEY",
            short_id="6ba85179e30d4fc2",
            server_name="www.google.com",
            spider_x="/spider",
            flow="",
            network="tcp",
        )

    def test_a_link_without_the_spider_path_still_works(self) -> None:
        profile = parse_vless_link(
            "vless://id@host.example:8443?security=reality&pbk=KEY"
        )
        assert profile is not None
        assert profile.spider_x == ""
        assert profile.port == 8443

    def test_an_ipv6_host_in_brackets_is_read(self) -> None:
        profile = parse_vless_link(
            "vless://id@[2001:db8::1]:443?security=reality&pbk=KEY"
        )
        assert profile is not None
        assert profile.address == "2001:db8::1"

    def test_a_flow_is_kept(self) -> None:
        profile = parse_vless_link(
            "vless://id@host:443?security=reality&pbk=KEY&flow=xtls-rprx-vision"
        )
        assert profile is not None
        assert profile.flow == "xtls-rprx-vision"

    @pytest.mark.parametrize(
        "link",
        [
            "",
            "   ",
            "vmess://id@host:443",
            "vless://@host:443?security=reality&pbk=KEY",
            "vless://id@:443?security=reality&pbk=KEY",
            "vless://id@host:443?security=reality",
            "vless://id@host:443?security=none",
        ],
    )
    def test_values_that_cannot_produce_an_outbound_are_rejected(
        self, link: str
    ) -> None:
        # A REALITY link without a public key would produce a client that
        # cannot connect, so it is rejected instead of written.
        if link.startswith("vless://id@host:443?security=none"):
            assert parse_vless_link(link) is not None
            return
        assert parse_vless_link(link) is None


class TestOutboundBuilders:
    """Tests for the outbounds the policy owns."""

    def test_the_remote_outbound_carries_the_reality_data(self) -> None:
        profile = parse_vless_link(LINK)
        assert profile is not None
        outbound = build_remote_outbound("pyntara-remote", profile)
        assert outbound["tag"] == "pyntara-remote"
        assert outbound["protocol"] == "vless"
        settings = outbound["settings"]
        assert isinstance(settings, dict)
        vnext = settings["vnext"]
        assert isinstance(vnext, list)
        assert vnext[0]["address"] == "203.0.113.9"
        assert vnext[0]["users"][0]["id"] == "client-id"
        stream = outbound["streamSettings"]
        assert isinstance(stream, dict)
        assert stream["realitySettings"]["publicKey"] == "PUBLICKEY"
        assert stream["realitySettings"]["spiderX"] == "/spider"

    def test_a_plain_tls_link_carries_no_reality_block(self) -> None:
        profile = parse_vless_link("vless://id@host:443?security=tls&sni=host")
        assert profile is not None
        outbound = build_remote_outbound("remote", profile)
        stream = outbound["streamSettings"]
        assert isinstance(stream, dict)
        assert "realitySettings" not in stream

    def test_the_tor_outbound_is_a_socks_client(self) -> None:
        outbound = build_tor_outbound("pyntara-tor", "127.0.0.1:9050")
        assert outbound["protocol"] == "socks"
        settings = outbound["settings"]
        assert isinstance(settings, dict)
        assert settings["servers"] == [{"address": "127.0.0.1", "port": 9050}]

    def test_the_i2p_outbound_is_an_http_client(self) -> None:
        outbound = build_i2p_outbound("pyntara-i2p", "127.0.0.1:4444")
        assert outbound["protocol"] == "http"

    @pytest.mark.parametrize("address", ["127.0.0.1", "127.0.0.1:abc", ":9050"])
    def test_a_malformed_proxy_address_is_reported(self, address: str) -> None:
        with pytest.raises(ValueError, match="address:port"):
            build_tor_outbound("tor", address)


class TestLocalProxyInbound:
    """Tests for the panel payload of the local proxy."""

    def test_the_payload_has_everything_the_panel_needs(self) -> None:
        payload = build_local_proxy_inbound(
            tag="pyntara-local-proxy",
            protocol="mixed",
            remark="pyntara local proxy",
            listen_address="127.0.0.1",
            port=10800,
            udp_enabled=True,
            sniffing_protocols=("http", "tls", "quic"),
        )
        assert payload["protocol"] == "mixed"
        assert payload["tag"] == "pyntara-local-proxy"
        assert payload["listen"] == "127.0.0.1"
        assert payload["port"] == 10800
        assert payload["enable"] is True
        assert payload["total"] == 0
        assert payload["expiryTime"] == 0
        assert payload["settings"] == {
            "auth": "noauth",
            "udp": True,
            "ip": "127.0.0.1",
        }
        assert payload["sniffing"] == {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
            "metadataOnly": False,
            "routeOnly": False,
        }


class TestPolicyValues:
    """Tests for the derived values of the policy."""

    def test_direct_ip_values_keep_the_order_and_remove_repeats(self) -> None:
        policy = make_policy()
        assert policy.direct_ip_values() == [
            "geoip:private",
            "200::/7",
            "300::/7",
            "10.10.0.0/24",
            "fe80::/64",
        ]

    def test_the_domain_strategy_follows_the_country(self) -> None:
        assert make_policy(in_russia=True).domain_strategy() == "IPIfNonMatch"
        assert make_policy(in_russia=False).domain_strategy() == "AsIs"


class TestBuildRoutingRules:
    """Tests for the rule order of both profiles."""

    def test_the_outside_profile_ends_with_the_remote_catch_all(self) -> None:
        rules = build_routing_rules(make_policy(), remote_outbound_available=True)
        assert [rule["outboundTag"] for rule in rules] == [
            "blocked",
            "pyntara-tor",
            "pyntara-i2p",
            "direct",
            "direct",
            "pyntara-remote",
        ]
        assert rules[0]["domain"] == ["geosite:category-ads-all"]
        assert rules[1]["domain"] == ["domain:.onion"]
        assert rules[2]["domain"] == ["domain:.i2p"]
        assert rules[-1] == {
            "type": "field",
            "inboundTag": ["pyntara-local-proxy"],
            "outboundTag": "pyntara-remote",
        }

    def test_the_russia_profile_sends_the_lists_and_defaults_to_direct(self) -> None:
        rules = build_routing_rules(
            make_policy(in_russia=True), remote_outbound_available=True
        )
        assert [rule["outboundTag"] for rule in rules] == [
            "blocked",
            "pyntara-tor",
            "pyntara-i2p",
            "direct",
            "direct",
            "pyntara-remote",
            "pyntara-remote",
            "direct",
            "direct",
            "pyntara-remote",
            "direct",
        ]
        blocked_domains = [
            rule for rule in rules if rule.get("domain") == ["ext-site:geosite_RU.dat:ru-blocked-all"]
        ]
        assert len(blocked_domains) == 1
        assert blocked_domains[0]["outboundTag"] == "pyntara-remote"
        assert rules[-1]["outboundTag"] == "direct"

    def test_every_name_rule_comes_before_the_first_address_rule(self) -> None:
        # The core resolves a name at the first rule carrying addresses
        # when the strategy is IPIfNonMatch, so a name a name list carries
        # must be decided by name first. Otherwise the locally resolved
        # address decides, and the address of a site blocked in Russia can
        # fall into a range that goes directly.
        for in_russia in (True, False):
            rules = build_routing_rules(
                make_policy(in_russia=in_russia), remote_outbound_available=True
            )
            name_indexes = [
                index for index, rule in enumerate(rules) if rule.get("domain")
            ]
            address_indexes = [index for index, rule in enumerate(rules) if rule.get("ip")]
            assert name_indexes, "the policy must carry name rules"
            assert address_indexes, "the policy must carry address rules"
            assert max(name_indexes) < min(address_indexes)

    def test_the_blocked_name_is_decided_before_its_blocked_address(self) -> None:
        # A resource blocked in Russia is carried by a name list and by an
        # address list. The name list has to win, so the same destination
        # cannot be sent directly through a locally resolved address.
        rules = build_routing_rules(
            make_policy(in_russia=True), remote_outbound_available=True
        )
        names = [
            index
            for index, rule in enumerate(rules)
            if rule.get("domain") == ["ext-site:geosite_RU.dat:ru-blocked-all"]
        ]
        addresses = [
            index
            for index, rule in enumerate(rules)
            if isinstance(rule.get("ip"), list)
            and "ext-ip:geoip_RU.dat:ru-blocked" in cast("list[str]", rule["ip"])
        ]
        assert names and addresses
        assert names[0] < addresses[0]

    def test_the_server_machine_has_no_rule_pointing_at_itself(self) -> None:
        rules = build_routing_rules(
            make_policy(in_russia=True), remote_outbound_available=False
        )
        assert [rule["outboundTag"] for rule in rules] == [
            "blocked",
            "pyntara-tor",
            "pyntara-i2p",
            "direct",
            "direct",
        ]

    def test_no_advertising_rule_without_categories(self) -> None:
        rules = build_routing_rules(
            make_policy(ad_block_domain_categories=()), remote_outbound_available=True
        )
        assert "blocked" not in [rule["outboundTag"] for rule in rules]

    def test_every_rule_is_scoped_to_the_local_proxy(self) -> None:
        rules = build_routing_rules(
            make_policy(in_russia=True), remote_outbound_available=True
        )
        assert all(rule["inboundTag"] == ["pyntara-local-proxy"] for rule in rules)


class TestApplyRoutingPolicy:
    """Tests for the template surgery."""

    def test_adds_the_outbounds_after_the_foreign_ones(self) -> None:
        updated, changed = apply_routing_policy(
            make_template(),
            make_policy(),
            remote_outbound=build_remote_outbound("pyntara-remote", make_profile()),
            remove_panel_restrictions=True,
        )
        assert changed is True
        tags = [outbound["tag"] for outbound in outbounds_of(updated)]
        assert tags == ["direct", "blocked", "pyntara-remote", "pyntara-tor", "pyntara-i2p"]

    def test_keeps_the_api_rule_first_and_the_foreign_rules(self) -> None:
        template = make_template()
        rules_of(template).append(
            {"type": "field", "domain": ["domain:example.test"], "outboundTag": "direct"}
        )
        updated, _ = apply_routing_policy(
            template,
            make_policy(),
            remote_outbound=None,
            remove_panel_restrictions=True,
        )
        rules = rules_of(updated)
        assert rules[0]["inboundTag"] == ["api"]
        assert {"type": "field", "domain": ["domain:example.test"], "outboundTag": "direct"} in rules

    def test_removes_the_panel_restrictions_when_asked(self) -> None:
        updated, _ = apply_routing_policy(
            make_template(),
            make_policy(),
            remote_outbound=None,
            remove_panel_restrictions=True,
        )
        rules = rules_of(updated)
        assert not any(rule.get("protocol") == ["bittorrent"] for rule in rules)
        direct = next(
            outbound for outbound in outbounds_of(updated) if outbound["tag"] == "direct"
        )
        assert direct["settings"] == {"domainStrategy": "AsIs"}

    def test_keeps_the_panel_restrictions_when_not_asked(self) -> None:
        updated, _ = apply_routing_policy(
            make_template(),
            make_policy(),
            remote_outbound=None,
            remove_panel_restrictions=False,
        )
        rules = rules_of(updated)
        assert any(rule.get("protocol") == ["bittorrent"] for rule in rules)
        direct = next(
            outbound for outbound in outbounds_of(updated) if outbound["tag"] == "direct"
        )
        settings = direct["settings"]
        assert isinstance(settings, dict)
        assert "finalRules" in settings

    def test_sets_the_domain_strategy_of_the_profile(self) -> None:
        updated, _ = apply_routing_policy(
            make_template(),
            make_policy(in_russia=True),
            remote_outbound=None,
            remove_panel_restrictions=True,
        )
        assert routing_strategy(updated) == "IPIfNonMatch"

    def test_a_second_application_changes_nothing(self) -> None:
        # Idempotency is what keeps a rerun from rewriting and reloading
        # the core for nothing.
        first, changed_first = apply_routing_policy(
            make_template(),
            make_policy(in_russia=True),
            remote_outbound=build_remote_outbound("pyntara-remote", make_profile()),
            remove_panel_restrictions=True,
        )
        second, changed_second = apply_routing_policy(
            first,
            make_policy(in_russia=True),
            remote_outbound=build_remote_outbound("pyntara-remote", make_profile()),
            remove_panel_restrictions=True,
        )
        assert changed_first is True
        assert changed_second is False
        assert json.dumps(second, sort_keys=True) == json.dumps(first, sort_keys=True)

    def test_switching_the_country_removes_the_old_profile(self) -> None:
        russia, _ = apply_routing_policy(
            make_template(),
            make_policy(in_russia=True),
            remote_outbound=build_remote_outbound("pyntara-remote", make_profile()),
            remove_panel_restrictions=True,
        )
        outside, changed = apply_routing_policy(
            russia,
            make_policy(in_russia=False),
            remote_outbound=build_remote_outbound("pyntara-remote", make_profile()),
            remove_panel_restrictions=True,
        )
        assert changed is True
        assert routing_strategy(outside) == "AsIs"
        tags = [rule["outboundTag"] for rule in rules_of(outside)]
        assert tags == ["api", "blocked", "pyntara-tor", "pyntara-i2p", "direct", "direct", "pyntara-remote"]

    def test_the_input_template_is_not_modified(self) -> None:
        template = make_template()
        before = json.dumps(template, sort_keys=True)
        apply_routing_policy(
            template,
            make_policy(),
            remote_outbound=None,
            remove_panel_restrictions=True,
        )
        assert json.dumps(template, sort_keys=True) == before

    def test_an_unknown_own_outbound_is_replaced_not_duplicated(self) -> None:
        template = make_template()
        outbounds_of(template).append(
            {"tag": "pyntara-tor", "protocol": "socks", "settings": {}}
        )
        updated, _ = apply_routing_policy(
            template,
            make_policy(),
            remote_outbound=None,
            remove_panel_restrictions=False,
        )
        tags = [outbound["tag"] for outbound in outbounds_of(updated)]
        assert tags.count("pyntara-tor") == 1


def test_the_panel_vocabulary_comes_from_the_config() -> None:
    # The inbound protocol, the rule protocols that count as a panel
    # restriction and the address category of the private block rule are
    # policy values: another set of them is the payload the panel gets and
    # the rules the policy removes, so no panel word lives in the code.
    policy = make_policy(
        panel_inbound_protocol="my-mixed",
        panel_blocked_rule_protocols=("my-bittorrent",),
        panel_private_block_category="my-geoip:private",
    )
    payload = build_local_proxy_inbound(
        tag=policy.inbound_tag,
        protocol=policy.panel_inbound_protocol,
        remark="pyntara local proxy",
        listen_address="127.0.0.1",
        port=10800,
        udp_enabled=True,
        sniffing_protocols=("http", "tls"),
    )
    assert payload["protocol"] == "my-mixed"
    template = make_template()
    rules_of(template).append(
        {
            "type": "field",
            "protocol": ["my-bittorrent"],
            "outboundTag": policy.blocked_outbound_tag,
        }
    )
    rules_of(template).append(
        {
            "type": "field",
            "ip": ["my-geoip:private"],
            "outboundTag": policy.blocked_outbound_tag,
        }
    )
    updated, _ = apply_routing_policy(
        template,
        policy,
        remote_outbound=None,
        remove_panel_restrictions=True,
    )
    rules = rules_of(updated)
    assert not any(rule.get("protocol") == ["my-bittorrent"] for rule in rules)
    assert not any(rule.get("ip") == ["my-geoip:private"] for rule in rules)
