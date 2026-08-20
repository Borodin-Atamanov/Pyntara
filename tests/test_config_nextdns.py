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
    assert section.resolve_section == "[Resolve]"
    assert section.dropin_header == (
        "# Managed by the Pyntara nextdns_setup_system_wide task."
    )
    assert section.domains_directive == "~."
    assert section.ipv4_servers == ("45.90.28.0", "45.90.30.0")
    assert section.ipv6_prefixes == ("2a07:a8c0", "2a07:a8c1")
    assert section.dot_endpoint_format == "{profile_id}.dns.nextdns.io"
    assert section.verification_url == "https://test.nextdns.io/"
    assert section.dns_over_tls == "opportunistic"
    assert section.fallback_dns == ("1.1.1.1", "8.8.8.8", "9.9.9.9")
    assert section.directive_keys == ("DNS", "FallbackDNS", "DNSOverTLS", "Domains")
    assert section.nmcli_check_command == ("nmcli", "--version")
    assert section.nmcli_list_command == (
        "nmcli",
        "-t",
        "-f",
        "NAME",
        "connection",
        "show",
    )
    assert section.nmcli_modify_command == (
        "nmcli",
        "connection",
        "modify",
        "{connection}",
        "ipv4.ignore-auto-dns",
        "{value}",
        "ipv6.ignore-auto-dns",
        "{value}",
    )
    assert section.restart_resolved_command == (
        "systemctl",
        "restart",
        "systemd-resolved",
    )
    assert section.resolvectl_status_command == ("resolvectl", "status")
    assert section.verification_command == (
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "{timeout}",
        "{url}",
    )
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
            'resolve_section = "[Resolve]"\n'
            'dropin_header = "# Managed by the Pyntara nextdns_setup_system_wide task."\n'
            'domains_directive = "~."\n'
            'ipv4_servers = ["45.90.28.0", "45.90.30.0"]\n'
            'ipv6_prefixes = ["2a07:a8c0", "2a07:a8c1"]\n'
            'dot_endpoint_format = "{profile_id}.dns.nextdns.io"\n'
            'verification_url = "https://test.nextdns.io/"\n'
            'dns_over_tls = "opportunistic"\n'
            "fallback_dns = [\n"
            '    "1.1.1.1",\n'
            '    "8.8.8.8",\n'
            '    "9.9.9.9",\n'
            "]\n"
            'directive_keys = ["DNS", "FallbackDNS", "DNSOverTLS", "Domains"]\n'
            'nmcli_check_command = ["nmcli", "--version"]\n'
            'nmcli_list_command = ["nmcli", "-t", "-f", "NAME", "connection", "show"]\n'
            'nmcli_modify_command = ["nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]\n'
            'restart_resolved_command = ["systemctl", "restart", "systemd-resolved"]\n'
            'resolvectl_status_command = ["resolvectl", "status"]\n'
            'verification_command = ["curl", "--fail", "--silent", "--show-error", "--max-time", "{timeout}", "{url}"]\n'
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
        # resolve_section is empty
        lambda c: c.replace('resolve_section = "[Resolve]"', 'resolve_section = ""'),
        # dropin_header is empty
        lambda c: c.replace(
            'dropin_header = "# Managed by the Pyntara nextdns_setup_system_wide task."',
            'dropin_header = ""',
        ),
        # domains_directive is empty
        lambda c: c.replace('domains_directive = "~."', 'domains_directive = ""'),
        # ipv4_servers is empty
        lambda c: c.replace(
            'ipv4_servers = ["45.90.28.0", "45.90.30.0"]', "ipv4_servers = []"
        ),
        # ipv4_servers has a non-string
        lambda c: c.replace(
            'ipv4_servers = ["45.90.28.0", "45.90.30.0"]', "ipv4_servers = [1]"
        ),
        # ipv6_prefixes is empty
        lambda c: c.replace(
            'ipv6_prefixes = ["2a07:a8c0", "2a07:a8c1"]', "ipv6_prefixes = []"
        ),
        # dot_endpoint_format lacks the placeholder
        lambda c: c.replace(
            'dot_endpoint_format = "{profile_id}.dns.nextdns.io"',
            'dot_endpoint_format = "dns.nextdns.io"',
        ),
        # dot_endpoint_format is empty
        lambda c: c.replace(
            'dot_endpoint_format = "{profile_id}.dns.nextdns.io"',
            'dot_endpoint_format = ""',
        ),
        # verification_url is empty
        lambda c: c.replace(
            'verification_url = "https://test.nextdns.io/"', 'verification_url = ""'
        ),
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
        # directive_keys is empty
        lambda c: c.replace(
            'directive_keys = ["DNS", "FallbackDNS", "DNSOverTLS", "Domains"]',
            "directive_keys = []",
        ),
        # nmcli_modify_command lacks the {connection} placeholder
        lambda c: c.replace(
            'nmcli_modify_command = ["nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]',
            'nmcli_modify_command = ["nmcli", "connection", "modify", "fixed"]',
        ),
        # verification_command lacks the {url} placeholder
        lambda c: c.replace(
            'verification_command = ["curl", "--fail", "--silent", "--show-error", "--max-time", "{timeout}", "{url}"]',
            'verification_command = ["curl", "--silent"]',
        ),
        # restart_resolved_command is empty
        lambda c: c.replace(
            'restart_resolved_command = ["systemctl", "restart", "systemd-resolved"]',
            "restart_resolved_command = []",
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
