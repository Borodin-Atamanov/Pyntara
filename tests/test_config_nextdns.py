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
    assert section.dnscrypt_config_path == Path(
        "/etc/dnscrypt-proxy/dnscrypt-proxy.toml"
    )
    assert section.profile_id_file_path == Path(
        "/var/lib/pyntara/nextdns_profile_id"
    )
    assert section.profile_id_file_mode == 0o644
    assert section.doh_url_format == "https://dns.nextdns.io/{profile_id}"
    assert section.verification_url == "https://test.nextdns.io/"
    assert section.restart_proxy_command == (
        "systemctl",
        "restart",
        "dnscrypt-proxy",
    )
    assert section.verification_command == (
        "curl",
        "--location",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "{timeout}",
        "{url}",
    )
    assert section.error_priority == 3
    assert section.command_timeout_seconds == 60


@pytest.mark.parametrize(
    "mutate",
    [
        # section is missing entirely
        lambda c: c.replace(
            "[nextdns_setup_system_wide]\n"
            'vault_group_title = "NextDNS"\n'
            'dnscrypt_config_path = "/etc/dnscrypt-proxy/dnscrypt-proxy.toml"\n'
            'profile_id_file_path = "/var/lib/pyntara/nextdns_profile_id"\n'
            'profile_id_file_mode = "0644"\n'
            'doh_url_format = "https://dns.nextdns.io/{profile_id}"\n'
            'verification_url = "https://test.nextdns.io/"\n'
            'restart_proxy_command = ["systemctl", "restart", "dnscrypt-proxy"]\n'
            'verification_command = ["curl", "--location", "--fail", "--silent", "--show-error", "--max-time", "{timeout}", "{url}"]\n'
            "error_priority = 3\n"
            "command_timeout_seconds = 60\n",
            "",
        ),
        # vault_group_title is empty
        lambda c: c.replace('vault_group_title = "NextDNS"', 'vault_group_title = ""'),
        # dnscrypt_config_path is a number
        lambda c: c.replace(
            'dnscrypt_config_path = "/etc/dnscrypt-proxy/dnscrypt-proxy.toml"',
            "dnscrypt_config_path = 1",
        ),
        # doh_url_format lacks the placeholder
        lambda c: c.replace(
            'doh_url_format = "https://dns.nextdns.io/{profile_id}"',
            'doh_url_format = "https://dns.nextdns.io/"',
        ),
        # doh_url_format is empty
        lambda c: c.replace(
            'doh_url_format = "https://dns.nextdns.io/{profile_id}"',
            'doh_url_format = ""',
        ),
        # verification_url is empty
        lambda c: c.replace(
            'verification_url = "https://test.nextdns.io/"', 'verification_url = ""'
        ),
        # restart_proxy_command is empty
        lambda c: c.replace(
            'restart_proxy_command = ["systemctl", "restart", "dnscrypt-proxy"]',
            "restart_proxy_command = []",
        ),
        # verification_command lacks the {url} placeholder
        lambda c: c.replace(
            'verification_command = ["curl", "--location", "--fail", "--silent", "--show-error", "--max-time", "{timeout}", "{url}"]',
            'verification_command = ["curl", "--silent"]',
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
