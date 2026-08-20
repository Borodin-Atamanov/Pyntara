"""Config tests for [dnscrypt_setup]."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


def test_load_config_dnscrypt_section_parses(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, base_config()))
    section = config.dnscrypt_setup
    assert section.package_name == "dnscrypt-proxy"
    assert section.config_path == Path("/etc/dnscrypt-proxy/dnscrypt-proxy.toml")
    assert section.service_unit_name == "dnscrypt-proxy.service"
    assert section.socket_unit_name == "dnscrypt-proxy.socket"
    assert section.socket_dropin_dir == Path(
        "/etc/systemd/system/dnscrypt-proxy.socket.d"
    )
    assert section.socket_dropin_file_name == "pyntara.conf"
    assert section.socket_dropin_file_mode == 0o644
    assert section.socket_section == "[Socket]"
    assert section.socket_dropin_header == (
        "# Managed by the Pyntara dnscrypt_setup task."
    )
    assert section.listen_address == "0.0.0.0:53053"
    assert section.fallback_resolvers == ("1.1.1.1", "8.8.8.8")
    assert section.resolved_conf_dir == Path("/etc/systemd/resolved.conf.d")
    assert section.dropin_file_name == "dnscrypt.conf"
    assert section.dropin_file_mode == 0o644
    assert section.resolve_section == "[Resolve]"
    assert section.dropin_header == (
        "# Managed by the Pyntara dnscrypt_setup task."
    )
    assert section.dns_directive == "DNS=127.0.0.1:53053"
    assert section.domains_directive == "~."
    assert section.directive_keys == ("DNS", "Domains")
    assert section.manage_networkmanager is True
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
    assert section.daemon_reload_command == ("systemctl", "daemon-reload")
    assert section.restart_resolved_command == (
        "systemctl",
        "restart",
        "systemd-resolved",
    )
    assert section.resolvectl_status_command == ("resolvectl", "status")
    assert section.verification_command == (
        "resolvectl",
        "query",
        "--cache=no",
        "--timeout",
        "{timeout}",
        "example.com",
    )
    assert section.install_retries == 3
    assert section.start_check_attempts == 5
    assert section.start_check_retry_delay_seconds == 1.0


@pytest.mark.parametrize(
    "mutate",
    [
        # section is missing entirely
        lambda c: c.replace(
            "[dnscrypt_setup]\n"
            'package_name = "dnscrypt-proxy"\n'
            'config_path = "/etc/dnscrypt-proxy/dnscrypt-proxy.toml"\n'
            'service_unit_name = "dnscrypt-proxy.service"\n'
            'socket_unit_name = "dnscrypt-proxy.socket"\n'
            'socket_dropin_dir = "/etc/systemd/system/dnscrypt-proxy.socket.d"\n'
            'socket_dropin_file_name = "pyntara.conf"\n'
            'socket_dropin_file_mode = "0644"\n'
            'socket_section = "[Socket]"\n'
            'socket_dropin_header = "# Managed by the Pyntara dnscrypt_setup task."\n'
            'listen_address = "0.0.0.0:53053"\n'
            'fallback_resolvers = ["1.1.1.1", "8.8.8.8"]\n'
            'resolved_conf_dir = "/etc/systemd/resolved.conf.d"\n'
            'dropin_file_name = "dnscrypt.conf"\n'
            'dropin_file_mode = "0644"\n'
            'resolve_section = "[Resolve]"\n'
            'dropin_header = "# Managed by the Pyntara dnscrypt_setup task."\n'
            'dns_directive = "DNS=127.0.0.1:53053"\n'
            'domains_directive = "~."\n'
            'directive_keys = ["DNS", "Domains"]\n'
            "manage_networkmanager = true\n"
            'nmcli_check_command = ["nmcli", "--version"]\n'
            'nmcli_list_command = ["nmcli", "-t", "-f", "NAME", "connection", "show"]\n'
            'nmcli_modify_command = ["nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]\n'
            'daemon_reload_command = ["systemctl", "daemon-reload"]\n'
            'restart_resolved_command = ["systemctl", "restart", "systemd-resolved"]\n'
            'resolvectl_status_command = ["resolvectl", "status"]\n'
            'verification_command = ["resolvectl", "query", "--cache=no", "--timeout", "{timeout}", "example.com"]\n'
            "install_retries = 3\n"
            "start_check_attempts = 5\n"
            "start_check_retry_delay_seconds = 1.0\n",
            "",
        ),
        # package_name is empty
        lambda c: c.replace(
            'package_name = "dnscrypt-proxy"', 'package_name = ""'
        ),
        # config_path is a number
        lambda c: c.replace(
            'config_path = "/etc/dnscrypt-proxy/dnscrypt-proxy.toml"',
            "config_path = 1",
        ),
        # socket_dropin_file_mode is not an octal string
        lambda c: c.replace(
            'socket_dropin_file_mode = "0644"', 'socket_dropin_file_mode = "999"'
        ),
        # listen_address is empty
        lambda c: c.replace(
            'listen_address = "0.0.0.0:53053"', 'listen_address = ""'
        ),
        # fallback_resolvers is empty
        lambda c: c.replace(
            'fallback_resolvers = ["1.1.1.1", "8.8.8.8"]', "fallback_resolvers = []"
        ),
        # fallback_resolvers has a non-string
        lambda c: c.replace(
            'fallback_resolvers = ["1.1.1.1", "8.8.8.8"]', "fallback_resolvers = [1]"
        ),
        # dns_directive is empty
        lambda c: c.replace(
            'dns_directive = "DNS=127.0.0.1:53053"', 'dns_directive = ""'
        ),
        # domains_directive is empty
        lambda c: c.replace('domains_directive = "~."', 'domains_directive = ""'),
        # directive_keys is empty
        lambda c: c.replace('directive_keys = ["DNS", "Domains"]', "directive_keys = []"),
        # manage_networkmanager is not a boolean
        lambda c: c.replace("manage_networkmanager = true", "manage_networkmanager = 1"),
        # nmcli_modify_command lacks the {connection} placeholder
        lambda c: c.replace(
            'nmcli_modify_command = ["nmcli", "connection", "modify", "{connection}", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]',
            'nmcli_modify_command = ["nmcli", "connection", "modify", "x", "ipv4.ignore-auto-dns", "{value}", "ipv6.ignore-auto-dns", "{value}"]',
        ),
        # verification_command lacks the {timeout} placeholder
        lambda c: c.replace(
            'verification_command = ["resolvectl", "query", "--cache=no", "--timeout", "{timeout}", "example.com"]',
            'verification_command = ["resolvectl", "query", "example.com"]',
        ),
        # install_retries is zero
        lambda c: c.replace("install_retries = 3", "install_retries = 0"),
        # start_check_attempts is zero
        lambda c: c.replace("start_check_attempts = 5", "start_check_attempts = 0"),
        # start_check_retry_delay_seconds is negative
        lambda c: c.replace(
            "start_check_retry_delay_seconds = 1.0",
            "start_check_retry_delay_seconds = -1",
        ),
    ],
)
def test_load_config_dnscrypt_wrong_types_raise(
    tmp_path: Path, mutate
) -> None:
    assert_config_error(tmp_path, mutate(base_config()))
