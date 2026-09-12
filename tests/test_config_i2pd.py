"""Config tests for the [i2pd_service_setup] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import (
    assert_config_error,
    base_config,
    load_checked_config,
    write_config,
)


def test_load_config_i2pd_traffic_limit_values(tmp_path: Path) -> None:
    # The typed traffic limit values: bandwidth in kilobytes per second
    # and the transit share in percent.
    config = load_checked_config(write_config(tmp_path, base_config()))
    assert config.i2pd_service_setup.bandwidth == 12500
    assert config.i2pd_service_setup.share == 1


def test_load_config_i2pd_asset_and_command_values(tmp_path: Path) -> None:
    # The asset name templates, the command lists, the template file names
    # and the identity wait round-trip from the shared document.
    config = load_checked_config(write_config(tmp_path, base_config()))
    section = config.i2pd_service_setup
    assert section.codename_asset_name_template.format(
        release_tag="2.55.0", codename="noble", arch="amd64"
    ) == "i2pd_2.55.0-1noble1_amd64.deb"
    assert section.generic_asset_name_template.format(
        release_tag="2.55.0", arch="amd64"
    ) == "i2pd_2.55.0-1_amd64.deb"
    assert section.os_release_codename_key == "VERSION_CODENAME"
    assert section.version_command == ("i2pd", "--version")
    assert section.service_enable_command == (
        "systemctl",
        "enable",
        "{service_unit_name}",
    )
    assert section.service_restart_command == (
        "systemctl",
        "restart",
        "{service_unit_name}",
    )
    assert section.config_template_file_name == "i2pd.conf"
    assert section.tunnels_template_file_name == "tunnels.conf"
    assert section.config_true_value == "true"
    assert section.config_false_value == "false"
    assert section.address_check_attempts == 10
    assert section.address_check_retry_delay_seconds == 2


@pytest.mark.parametrize(
    "content",
    [
        # bandwidth is a string, not an integer
        base_config().replace("bandwidth = 12500\n", 'bandwidth = "12500"\n'),
        # bandwidth is a boolean
        base_config().replace("bandwidth = 12500\n", "bandwidth = true\n"),
        # bandwidth is zero, which i2pd maps to the lowest bandwidth class
        base_config().replace("bandwidth = 12500\n", "bandwidth = 0\n"),
        # bandwidth is negative
        base_config().replace("bandwidth = 12500\n", "bandwidth = -1\n"),
        # share is a string, not an integer
        base_config().replace("share = 1\n", 'share = "1"\n'),
        # share is negative
        base_config().replace("share = 1\n", "share = -1\n"),
        # share exceeds the 100 percent maximum
        base_config().replace("share = 1\n", "share = 101\n"),
    ],
)
def test_load_config_i2pd_wrong_traffic_limit_raises(
    tmp_path: Path, content: str
) -> None:
    assert_config_error(tmp_path, content)
