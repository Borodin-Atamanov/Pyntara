"""NextDNS profile selection.

The system-wide NextDNS task derives one profile per machine from the
hostname: the profile ID is chosen deterministically by
sha256(hostname) modulo the number of profiles in the vault group, so the
same hostname always resolves through the same account and the choice
spreads machines evenly over the profile pool (docs/spec/nextdns-profile.md).
The profile ID shape (six lowercase hex digits) is a format invariant of
the NextDNS service and stays in code as an approved exception
(architecture contract section 3); the vault group title lives in the
[nextdns_setup_system_wide] config table. The module is the single
implementation of the selection; the task and the tests import it, never
copy it.
"""

from __future__ import annotations

import hashlib
import re

# A NextDNS profile ID is exactly six lowercase hex digits. The shape is
# a format invariant of the NextDNS service, approved as a code exception
# in the architecture contract (section 3).
PROFILE_ID_RE = re.compile(r"^[0-9a-f]{6}$")


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



