"""Config tests for [upnp_forwarding_setup]."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from config_helpers import (
    assert_config_error,
    base_config,
    load_checked_config,
    write_config,
)


def test_load_config_upnp_forwarding_section_parses(tmp_path: Path) -> None:
    config = load_checked_config(write_config(tmp_path, base_config()))
    section = config.upnp_forwarding_setup
    assert section.upnp_package == "miniupnpc"
    assert section.upnp_client_command == "upnpc"
    assert section.upnp_protocol == "TCP"
    assert section.upnp_mapping_description == "pyntara ssh"
    assert section.mapping_attempts == 11
    assert section.service_unit_name == "upnp_forwarding.service"
    assert section.timer_unit_name == "upnp_forwarding.timer"
    assert section.service_template_file_name == "upnp_forwarding.service"
    assert section.timer_template_file_name == "upnp_forwarding.timer"
    assert section.service_module_name == "pyntara.upnp_forwarding"
    assert section.module_run_command == (
        "{python}",
        "-m",
        "{module}",
        "{config_path}",
    )
    assert section.systemctl_start_command == (
        "systemctl",
        "start",
        "--no-block",
        "{unit_name}",
    )
    assert section.timer_boot_delay_seconds == 90
    assert section.timer_interval_seconds == 900
    assert section.journal_identifier == "upnp_forwarding"
    assert section.error_priority == 3
    assert section.report_channel_name == "upnp"
    assert section.global_scope_name == "global"
    assert section.nat_scope_name == "nat"


@pytest.mark.parametrize(
    "mutate",
    [
        # the section is not there under its own name
        lambda c: c.replace("[upnp_forwarding_setup]", "[upnp_forwarding]"),
        # mapping_attempts is zero
        lambda c: c.replace("mapping_attempts = 11", "mapping_attempts = 0"),
        # the enable command lost the unit name placeholder
        lambda c: c.replace(
            'systemctl_enable_command = ["systemctl", "enable", "{unit_name}"]',
            'systemctl_enable_command = ["systemctl", "enable"]',
        ),
        # the run command lost the config path placeholder
        lambda c: c.replace(
            'module_run_command = ["{python}", "-m", "{module}", "{config_path}"]',
            'module_run_command = ["{python}", "-m", "{module}"]',
        ),
        # timer_interval_seconds is zero
        lambda c: c.replace(
            "timer_interval_seconds = 900", "timer_interval_seconds = 0"
        ),
        # error_priority is outside the syslog range
        lambda c: c.replace(
            'journal_identifier = "upnp_forwarding"\nerror_priority = 3',
            'journal_identifier = "upnp_forwarding"\nerror_priority = 9',
        ),
        # the two scope names are the same, so the report would hide the scope
        lambda c: c.replace(
            'nat_scope_name = "nat"', 'nat_scope_name = "global"'
        ),
    ],
)
def test_load_config_upnp_forwarding_rejects(
    mutate: Callable[[str], str], tmp_path: Path
) -> None:
    assert_config_error(tmp_path, mutate(base_config()))
