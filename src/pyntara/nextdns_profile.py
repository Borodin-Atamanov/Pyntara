# Shared NextDNS profile vault selection.

from __future__ import annotations

import socket

from pykeepass import PyKeePass

from pyntara.nextdns import select_profile_id


def select_profile_from_vault(kp: PyKeePass, group_title: str) -> str | None:
    # Select the deterministic profile ID from a vault group.

    group = kp.find_groups(name=group_title, first=True)
    if group is None:
        return None
    profile_ids = tuple(
        sorted(
            (
                entry.username.strip()
                for entry in group.entries
                if entry.username and entry.username.strip()
            ),
            key=str.casefold,
        )
    )
    if not profile_ids:
        return None
    return select_profile_id(socket.gethostname(), profile_ids)
