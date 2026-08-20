"""Unit tests for the NextDNS profile selection and endpoint derivation.

The endpoint formulas and the profile selection are pure functions with
no external dependencies; the tests pin the fixed values that NextDNS
documents for a profile ID.
"""

from __future__ import annotations

import pytest

from pyntara.nextdns import (
    dot_endpoint,
    ipv6_addresses,
    profile_id_is_valid,
    resolve_servers,
    select_profile_id,
)

VALID_ID = "6c7f39"
IPV4_SERVERS = ("45.90.28.0", "45.90.30.0")
IPV6_PREFIXES = ("2a07:a8c0", "2a07:a8c1")
DOT_ENDPOINT_FORMAT = "{profile_id}.dns.nextdns.io"


def test_profile_id_is_valid_accepts_six_hex() -> None:
    assert profile_id_is_valid("6c7f39") is True
    assert profile_id_is_valid("000000") is True
    assert profile_id_is_valid("ABCDEF") is False
    assert profile_id_is_valid("12345") is False
    assert profile_id_is_valid("1234567") is False
    assert profile_id_is_valid("zzzzzz") is False


def test_dot_endpoint_embeds_profile_id() -> None:
    # The DoT endpoint is the TLS server name that identifies the profile.
    assert dot_endpoint(VALID_ID, DOT_ENDPOINT_FORMAT) == "6c7f39.dns.nextdns.io"


def test_dot_endpoint_rejects_malformed_id() -> None:
    with pytest.raises(ValueError):
        dot_endpoint("nope", DOT_ENDPOINT_FORMAT)


def test_dot_endpoint_uses_custom_format() -> None:
    # The format comes from the config: a different pattern is honored.
    assert dot_endpoint(VALID_ID, "dns-{profile_id}.example.net") == (
        "dns-6c7f39.example.net"
    )


def test_ipv6_addresses_embed_profile_bytes() -> None:
    # 6c 7f 39 -> first byte 6c, two low bytes 7f39, under both prefixes.
    assert ipv6_addresses(VALID_ID, IPV6_PREFIXES) == (
        "2a07:a8c0::6c:7f39",
        "2a07:a8c1::6c:7f39",
    )


def test_ipv6_addresses_lowest_and_highest_ids() -> None:
    assert ipv6_addresses("000001", IPV6_PREFIXES) == (
        "2a07:a8c0::0:1",
        "2a07:a8c1::0:1",
    )
    assert ipv6_addresses("ffffff", IPV6_PREFIXES) == (
        "2a07:a8c0::ff:ffff",
        "2a07:a8c1::ff:ffff",
    )


def test_ipv6_addresses_use_custom_prefixes() -> None:
    # The prefixes come from the config.
    assert ipv6_addresses("000001", ("2606:4700",)) == ("2606:4700::0:1",)


def test_resolve_servers_carry_tls_name() -> None:
    # Every entry is address#endpoint, so systemd-resolved connects to the
    # anycast address and identifies the profile through the TLS name.
    servers = resolve_servers(
        VALID_ID, IPV4_SERVERS, IPV6_PREFIXES, DOT_ENDPOINT_FORMAT
    )
    assert servers == (
        "45.90.28.0#6c7f39.dns.nextdns.io",
        "45.90.30.0#6c7f39.dns.nextdns.io",
        "2a07:a8c0::6c:7f39#6c7f39.dns.nextdns.io",
        "2a07:a8c1::6c:7f39#6c7f39.dns.nextdns.io",
    )


def test_select_profile_id_is_deterministic() -> None:
    pool = ("111111", "222222", "333333", "aaaaaa", "bbbbbb")
    first = select_profile_id("myhost", pool)
    second = select_profile_id("myhost", pool)
    assert first == second
    assert first in pool


def test_select_profile_id_spreads_over_pool() -> None:
    # Different hostnames must not collapse onto a single profile: the
    # sha256 mod pool selection is spread by construction, and a few dozen
    # hostnames should touch at least two profiles of a small pool.
    pool = ("111111", "222222", "333333", "aaaaaa", "bbbbbb")
    chosen = {select_profile_id(f"host-{index}", pool) for index in range(50)}
    assert len(chosen) > 1


def test_select_profile_id_empty_pool_returns_none() -> None:
    assert select_profile_id("myhost", ()) is None


def test_select_profile_id_broken_id_returns_none() -> None:
    # A pool entry that is not a valid profile ID must never be selected.
    assert select_profile_id("myhost", ("zzzzzz",)) is None
    assert select_profile_id("myhost", ("12345",)) is None
