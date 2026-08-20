"""NextDNS profile selection and endpoint derivation.

The system-wide NextDNS task derives one profile per machine from the
hostname: the profile ID is chosen deterministically by
sha256(hostname) modulo the number of profiles in the vault group, so the
same hostname always resolves through the same account and the choice
spreads machines evenly over the profile pool. The endpoint formulas are
fixed by the NextDNS service (docs/spec/networking.md): the DoT endpoint
is <id>.dns.nextdns.io, the id-specific IPv6 addresses embed the profile
ID bytes, and the IPv4 anycast addresses 45.90.28.0 and 45.90.30.0 carry
the profile only through the TLS server name, never through the address.
The module is the single implementation of these formulas; the task and
the tests import them, never copy them.
"""

from __future__ import annotations

import hashlib
import re

# A NextDNS profile ID is exactly six lowercase hex digits.
PROFILE_ID_RE = re.compile(r"^[0-9a-f]{6}$")

# The two IPv4 anycast addresses of the NextDNS service; the profile is
# carried by the TLS server name (#<id>.dns.nextdns.io), not by the address.
NEXTDNS_IPV4_SERVERS: tuple[str, ...] = ("45.90.28.0", "45.90.30.0")

# The two IPv6 anycast prefixes of the NextDNS service; the profile ID is
# embedded in the last three bytes of the address.
NEXTDNS_IPV6_PREFIXES: tuple[str, ...] = ("2a07:a8c0", "2a07:a8c1")

# The DoT endpoint pattern: <id>.dns.nextdns.io.
DOT_ENDPOINT_FORMAT = "{profile_id}.dns.nextdns.io"


def profile_id_is_valid(profile_id: str) -> bool:
    """True when the value is a valid six-hex NextDNS profile ID."""

    return bool(PROFILE_ID_RE.match(profile_id))


def select_profile_id(hostname: str, profile_ids: tuple[str, ...]) -> str | None:
    """The profile ID for a hostname from a sorted ID pool, or None.

    The pool is the sorted profile ID tuple of the vault group. The index
    is sha256(hostname) modulo the pool size, so the choice is
    deterministic and spreads hostnames evenly; a None profile_id in the
    pool or an empty pool yields None, so the caller fails loudly instead
    of picking a broken profile. The hostname is used as-is, exactly as
    the machine reports it.
    """

    if not profile_ids:
        return None
    digest = int.from_bytes(hashlib.sha256(hostname.encode("utf-8")).digest(), "big")
    chosen = profile_ids[digest % len(profile_ids)]
    if chosen is None or not profile_id_is_valid(chosen):
        return None
    return chosen


def dot_endpoint(profile_id: str) -> str:
    """The DNS-over-TLS endpoint name of a profile.

    The endpoint is <id>.dns.nextdns.io; it is the TLS server name of the
    DoT connection, the identifier that tells NextDNS which profile
    answers. A malformed profile ID is a programming error and raises.
    """

    if not profile_id_is_valid(profile_id):
        raise ValueError(f"invalid NextDNS profile ID: {profile_id!r}")
    return DOT_ENDPOINT_FORMAT.format(profile_id=profile_id)


def ipv6_addresses(profile_id: str) -> tuple[str, ...]:
    """The id-specific IPv6 addresses of a profile.

    Each of the two prefixes carries the profile ID in the last three
    bytes, split as the first byte and the two remaining bytes:
    2a07:a8c0::<b1>:<b2b3>. The addresses are the same values that the
    NextDNS account page lists and that the accounts CSV carries in the
    notes; they let a client reach the profile over IPv6 without a TLS
    name, although the DoT configuration still sends the endpoint name.
    """

    if not profile_id_is_valid(profile_id):
        raise ValueError(f"invalid NextDNS profile ID: {profile_id!r}")
    high, middle, low = (int(profile_id[index : index + 2], 16) for index in (0, 2, 4))
    return tuple(
        f"{prefix}::{high:x}:{middle * 256 + low:x}" for prefix in NEXTDNS_IPV6_PREFIXES
    )


def resolve_servers(profile_id: str) -> tuple[str, ...]:
    """The systemd-resolved DNS= entries of a profile.

    Every entry carries the TLS server name after the hash, so
    systemd-resolved connects to the NextDNS anycast address and identifies
    the profile through the endpoint name: the two IPv4 addresses and the
    two id-specific IPv6 addresses. The entries are the DoT configuration
    that resolved.conf.d drop-ins carry.
    """

    endpoint = dot_endpoint(profile_id)
    return tuple(
        f"{server}#{endpoint}" for server in (*NEXTDNS_IPV4_SERVERS, *ipv6_addresses(profile_id))
    )
