"""Config tests for the [sotavpn_setup] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import (
    assert_config_error,
    base_config,
    load_checked_config,
    write_config,
)


@pytest.mark.parametrize(
    "content",
    [
        # username is a number, not a string
        base_config().replace(
            '[sotavpn_setup]\nusername = "i"',
            "[sotavpn_setup]\nusername = 42",
        ),
        # archive_url is empty
        base_config().replace(
            'archive_url = "https://codeload.github.com/Borodin-Atamanov/'
            'sotavpn-subscription-for-any-client/tar.gz/refs/heads/main"',
            'archive_url = ""',
        ),
        # installer_command is a string, not an array
        base_config().replace(
            'installer_command = ["{python}", "{installer_path}", "install"]',
            'installer_command = "install"',
        ),
        # service_unit_name is empty
        base_config().replace(
            'service_unit_name = "sotavpn-bridge.service"',
            'service_unit_name = ""',
        ),
        # settings_version_key is empty
        base_config().replace(
            'settings_version_key = "PROGRAM_VERSION"',
            'settings_version_key = ""',
        ),
        # subscription_update_interval_seconds is a string
        base_config().replace(
            "subscription_update_interval_seconds = 300",
            'subscription_update_interval_seconds = "300"',
        ),
        # subscription_update_interval_seconds is zero
        base_config().replace(
            "subscription_update_interval_seconds = 300",
            "subscription_update_interval_seconds = 0",
        ),
        # subscription_allow_private is a string, not a boolean
        base_config().replace(
            "subscription_allow_private = true",
            'subscription_allow_private = "true"',
        ),
        # balancer_tag carries whitespace
        base_config().replace(
            'balancer_tag = "pyntara-fastest"', 'balancer_tag = "fastest pool"'
        ),
        # balancer_tag is empty
        base_config().replace(
            'balancer_tag = "pyntara-fastest"', 'balancer_tag = ""'
        ),
        # bridge_ready_wait_seconds is a string
        base_config().replace(
            "bridge_ready_wait_seconds = 60",
            'bridge_ready_wait_seconds = "60"',
        ),
        # readiness_check_delay_seconds is negative
        base_config().replace(
            "bridge_ready_wait_seconds = 60\nreadiness_check_delay_seconds = 2",
            "bridge_ready_wait_seconds = 60\nreadiness_check_delay_seconds = -1",
        ),
        # observatory_probe_interval is empty
        base_config().replace(
            'observatory_probe_interval = "30s"',
            'observatory_probe_interval = ""',
        ),
        # key_entry_title names no entry of the [vault_structure] table
        base_config().replace(
            'key_entry_title = "sotavpn_uuid"',
            'key_entry_title = "no_such_entry"',
        ),
    ],
)
def test_load_config_wrong_values_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_sotavpn_values(tmp_path: Path) -> None:
    # The typed values round-trip from the config document.
    config = load_checked_config(write_config(tmp_path, base_config()))
    assert config.sotavpn_setup.username == "i"
    assert config.sotavpn_setup.home_dir == "/home/i"
    assert config.sotavpn_setup.installer_file_name == "install_sotavpn_bridge.py"
    assert config.sotavpn_setup.service_unit_name == "sotavpn-bridge.service"
    assert config.sotavpn_setup.key_entry_title == "sotavpn_uuid"
    assert config.sotavpn_setup.subscription_tag_prefix == "sota-"
    assert config.sotavpn_setup.subscription_update_interval_seconds == 300
    assert config.sotavpn_setup.subscription_allow_private is True
    assert config.sotavpn_setup.balancer_tag == "pyntara-fastest"
    assert config.sotavpn_setup.observatory_probe_interval == "30s"
    assert config.sotavpn_setup.bridge_ready_wait_seconds == 60
    assert config.sotavpn_setup.readiness_check_delay_seconds == 2
