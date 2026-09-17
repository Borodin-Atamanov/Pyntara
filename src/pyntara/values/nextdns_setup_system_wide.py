"""Values of the nextdns_setup_system_wide task.

The task picks one NextDNS profile per machine from the vault group named here,
deterministically from the hostname, and records the profile ID in the file
below. The system-wide resolver is owned by the dnsproxy_setup task, which
reads the recorded ID (docs/spec/nextdns-profile.md); the path and the mode of
that file are shared with it and live in the shared module.
"""

from __future__ import annotations

# Title of the vault subgroup that carries the NextDNS profile accounts.
VAULT_GROUP_TITLE: str = "NextDNS"

# Syslog priority of a serious failure, 0 to 7.
ERROR_PRIORITY: int = 3

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "VAULT_GROUP_TITLE",
    "ERROR_PRIORITY",
)
