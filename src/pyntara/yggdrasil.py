"""Shared Yggdrasil helpers: parse the node self address.

The yggdrasil_service_setup task and the deployed address command
(pyntara.yggdrasil_address) both need the node address reported by the
admin socket. The JSON parsing lives here, shared and imported, never
copied (architecture contract section 3).
"""

from __future__ import annotations

import json


def self_address_from_output(output: str) -> str | None:
    """The yggdrasil self address from a yggdrasilctl getSelf output, or None.

    The admin socket reports the node state as JSON with the self field
    carrying the address. An unparsable payload or a missing or
    non-string self field yields None, so the caller falls back to the
    saved address file instead of failing.
    """

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    address = data.get("self") if isinstance(data, dict) else None
    if not isinstance(address, str) or not address:
        return None
    return address
