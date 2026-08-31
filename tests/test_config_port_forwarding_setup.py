"""Config tests for [port_forwarding_setup]."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


def test_load_config_port_forwarding_section_parses(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, base_config()))
    section = config.port_forwarding_setup
    assert section.vault_group_title == "port_forwarding_servers"
    assert section.passphrase_entry_title == "ssh_passphase_for_port_forwarding"
    assert section.remote_ssh_user == "i"
    assert section.desired_port_min == 40000
    assert section.desired_port_max == 49999
    assert section.server_alive_interval_seconds == 61
    assert section.server_alive_count_max == 3
    assert section.connect_timeout_seconds == 31
    assert section.backoff_base_seconds == 2
    assert section.backoff_multiplier == 2
    assert section.backoff_max_seconds == 1024
    assert section.state_file_path == Path(
        "/var/lib/pyntara/port_forwarding_state.json"
    )
    assert section.service_unit_name == "auto_port_forwarding.service"
    assert section.service_restart_seconds == 30
    assert section.journal_identifier == "auto_port_forwarding"
    assert section.error_priority == 3


@pytest.mark.parametrize(
    "mutate",
    [
        # section is missing entirely
        lambda c: c.replace(
            "[port_forwarding_setup]\n"
            'vault_group_title = "port_forwarding_servers"\n'
            'passphrase_entry_title = "ssh_passphase_for_port_forwarding"\n'
            'remote_ssh_user = "i"\n'
            "desired_port_min = 40000\n"
            "desired_port_max = 49999\n"
            "server_alive_interval_seconds = 61\n"
            "server_alive_count_max = 3\n"
            "connect_timeout_seconds = 31\n"
            "backoff_base_seconds = 2\n"
            "backoff_multiplier = 2\n"
            "backoff_max_seconds = 1024\n"
            'state_file_path = "/var/lib/pyntara/port_forwarding_state.json"\n'
            'service_unit_name = "auto_port_forwarding.service"\n'
            "service_restart_seconds = 30\n"
            'journal_identifier = "auto_port_forwarding"\n'
            "error_priority = 3\n",
            "",
        ),
        # vault_group_title is empty
        lambda c: c.replace(
            'vault_group_title = "port_forwarding_servers"',
            'vault_group_title = ""',
        ),
        # passphrase_entry_title is empty
        lambda c: c.replace(
            'passphrase_entry_title = "ssh_passphase_for_port_forwarding"',
            'passphrase_entry_title = ""',
        ),
        # remote_ssh_user is empty
        lambda c: c.replace('remote_ssh_user = "i"', 'remote_ssh_user = ""'),
        # desired_port_min is zero
        lambda c: c.replace("desired_port_min = 40000", "desired_port_min = 0"),
        # desired_port_max is out of the TCP port space
        lambda c: c.replace(
            "desired_port_max = 49999", "desired_port_max = 70000"
        ),
        # desired_port_min exceeds desired_port_max
        lambda c: c.replace(
            "desired_port_min = 40000", "desired_port_min = 50000"
        ),
        # server_alive_interval_seconds is zero
        lambda c: c.replace(
            "server_alive_interval_seconds = 61", "server_alive_interval_seconds = 0"
        ),
        # server_alive_count_max is zero
        lambda c: c.replace("server_alive_count_max = 3", "server_alive_count_max = 0"),
        # backoff_multiplier is one
        lambda c: c.replace("backoff_multiplier = 2", "backoff_multiplier = 1"),
        # state_file_path is empty
        lambda c: c.replace(
            'state_file_path = "/var/lib/pyntara/port_forwarding_state.json"',
            'state_file_path = ""',
        ),
        # service_restart_seconds is negative
        lambda c: c.replace(
            "service_restart_seconds = 30", "service_restart_seconds = -5"
        ),
        # journal_identifier is empty
        lambda c: c.replace(
            'journal_identifier = "auto_port_forwarding"', 'journal_identifier = ""'
        ),
        # error_priority is out of range
        lambda c: c.replace("error_priority = 3", "error_priority = 9"),
    ],
)
def test_load_config_port_forwarding_wrong_types_raise(
    tmp_path: Path, mutate: Callable[[str], str]
) -> None:
    assert_config_error(tmp_path, mutate(base_config()))


def test_load_config_passphrase_title_must_exist_in_vault_structure(
    tmp_path: Path,
) -> None:
    # The port-forwarding passphrase entry title must be part of the vault
    # structure: a typo is caught at config load, not on the target machine.
    assert_config_error(
        tmp_path,
        base_config().replace(
            'passphrase_entry_title = "ssh_passphase_for_port_forwarding"',
            'passphrase_entry_title = "no_such_entry"',
        ),
        match="must name an entry",
    )
