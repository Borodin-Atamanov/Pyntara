"""Config tests for the [i2pd_service_setup] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


def test_load_config_i2pd_traffic_limit_values(tmp_path: Path) -> None:
    # The typed traffic limit values: bandwidth in kilobytes per second
    # and the transit share in percent.
    config = load_config(write_config(tmp_path, base_config()))
    assert config.i2pd_service_setup.bandwidth == 12500
    assert config.i2pd_service_setup.share == 1


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
