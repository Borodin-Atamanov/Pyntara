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
    assert section.resolved_conf_dir == Path("/etc/systemd/resolved.conf.d")
    assert section.dropin_file_name == "pyntara.conf"
    assert section.dropin_file_mode == 0o644
    assert section.dns_over_tls == "opportunistic"
    assert section.fallback_dns == ("1.1.1.1", "8.8.8.8", "9.9.9.9")
    assert section.manage_networkmanager is True
    assert section.error_priority == 3
    assert section.command_timeout_seconds == 60


@pytest.mark.parametrize(
    "mutate",
    [
        # section is missing entirely
        lambda c: c.replace(
            "[nextdns_setup_system_wide]\n"
            'vault_group_title = "NextDNS"\n'
            'resolved_conf_dir = "/etc/systemd/resolved.conf.d"\n'
            'dropin_file_name = "pyntara.conf"\n'
            'dropin_file_mode = "0644"\n'
            'dns_over_tls = "opportunistic"\n'
            "fallback_dns = [\n"
            '    "1.1.1.1",\n'
            '    "8.8.8.8",\n'
            '    "9.9.9.9",\n'
            "]\n"
            "manage_networkmanager = true\n"
            "error_priority = 3\n"
            "command_timeout_seconds = 60\n",
            "",
        ),
        # vault_group_title is empty
        lambda c: c.replace('vault_group_title = "NextDNS"', 'vault_group_title = ""'),
        # resolved_conf_dir is a number
        lambda c: c.replace(
            'resolved_conf_dir = "/etc/systemd/resolved.conf.d"',
            "resolved_conf_dir = 1",
        ),
        # dropin_file_name is empty
        lambda c: c.replace('dropin_file_name = "pyntara.conf"', 'dropin_file_name = ""'),
        # dropin_file_mode is not an octal string
        lambda c: c.replace('dropin_file_mode = "0644"', 'dropin_file_mode = "999"'),
        # dns_over_tls is not in the vocabulary
        lambda c: c.replace('dns_over_tls = "opportunistic"', 'dns_over_tls = "always"'),
        # fallback_dns is empty
        lambda c: c.replace(
            "fallback_dns = [\n"
            '    "1.1.1.1",\n'
            '    "8.8.8.8",\n'
            '    "9.9.9.9",\n'
            "]",
            "fallback_dns = []",
        ),
        # fallback_dns has a non-string
        lambda c: c.replace(
            "fallback_dns = [\n"
            '    "1.1.1.1",\n'
            '    "8.8.8.8",\n'
            '    "9.9.9.9",\n'
            "]",
            "fallback_dns = [1]",
        ),
        # manage_networkmanager is a string, not a boolean
        lambda c: c.replace(
            "manage_networkmanager = true", 'manage_networkmanager = "yes"'
        ),
        # error_priority is out of range
        lambda c: c.replace("error_priority = 3", "error_priority = 9"),
        # command_timeout_seconds is not positive
        lambda c: c.replace(
            "command_timeout_seconds = 60", "command_timeout_seconds = 0"
        ),
    ],
)
def test_load_config_nextdns_wrong_types_raise(
    tmp_path: Path, mutate: object
) -> None:
    assert_config_error(tmp_path, mutate(base_config()))
