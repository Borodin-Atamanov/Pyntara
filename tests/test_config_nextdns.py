"""Config tests for [nextdns_setup_system_wide]."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


def test_load_config_nextdns_section_parses(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, base_config()))
    section = config.nextdns_setup_system_wide
    assert section.vault_group_title == "NextDNS"
    assert section.profile_id_file_path == Path(
        "/var/lib/pyntara/nextdns_profile_id"
    )
    assert section.profile_id_file_mode == 0o644
    assert section.error_priority == 3


@pytest.mark.parametrize(
    "mutate",
    [
        # section is missing entirely
        lambda c: c.replace(
            "[nextdns_setup_system_wide]\n"
            'vault_group_title = "NextDNS"\n'
            'profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"\n'
            'profile_id_file_mode = "0644"\n'
            "error_priority = 3\n",
            "",
        ),
        # vault_group_title is empty
        lambda c: c.replace('vault_group_title = "NextDNS"', 'vault_group_title = ""'),
        # profile_id_file_path is empty
        lambda c: c.replace(
            'profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"',
            'profile_id_file_path = ""',
        ),
        # profile_id_file_mode is not a valid octal mode
        lambda c: c.replace(
            'profile_id_file_mode = "0644"', 'profile_id_file_mode = "999"'
        ),
        # error_priority is out of range
        lambda c: c.replace("error_priority = 3", "error_priority = 9"),
    ],
)
def test_load_config_nextdns_wrong_types_raise(
    tmp_path: Path, mutate: object
) -> None:
    assert_config_error(tmp_path, mutate(base_config()))
