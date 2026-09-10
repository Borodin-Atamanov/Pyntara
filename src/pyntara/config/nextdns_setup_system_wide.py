"""[nextdns_setup_system_wide] table parser.

The section carries the parameters of the NextDNS profile selection
task: the vault group that holds the profile accounts, the path and mode
of the file that records the selected profile ID and the syslog priority
of a serious failure.
"""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NextdnsSetupSystemWideConfig:
    """NextDNS profile selection parameters for the nextdns_setup_system_wide task.

    vault_group_title names the vault subgroup that carries the NextDNS
    profile accounts (the [vault_structure] groups). profile_id_file_path
    and profile_id_file_mode are the path and mode of the file that
    records the selected profile ID for dnsproxy_setup and the System
    Metrics collector. error_priority is the syslog priority of a serious
    failure.
    """

    vault_group_title: str
    profile_id_file_path: Path
    profile_id_file_mode: int
    error_priority: int
