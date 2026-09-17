"""Values of the nextdns_setup_system_wide task.

The task picks one NextDNS profile per machine from the vault group named here,
deterministically from the hostname, and records the profile ID in the file
below. The system-wide resolver is owned by the dnsproxy_setup task, which
reads the recorded ID (docs/spec/nextdns-profile.md).
"""

from __future__ import annotations

from pathlib import Path

# Title of the vault subgroup that carries the NextDNS profile accounts.
VAULT_GROUP_TITLE: str = "NextDNS"

# Path of the file that records the selected NextDNS profile ID. dnsproxy_setup
# and the System Metrics collector read it (docs/spec/system-metrics.md,
# section Collected data).
PROFILE_ID_FILE_PATH: Path = Path("/var/lib/pyntara/nextdns_profile_id")

# File mode of the recorded profile ID file: readable by the collector that
# reads the ID, not writable by it.
PROFILE_ID_FILE_MODE: int = 0o644

# Syslog priority of a serious failure, 0 to 7.
ERROR_PRIORITY: int = 3

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "VAULT_GROUP_TITLE",
    "PROFILE_ID_FILE_PATH",
    "PROFILE_ID_FILE_MODE",
    "ERROR_PRIORITY",
)
