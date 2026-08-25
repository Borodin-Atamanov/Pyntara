"""Config tests for the [three_x_ui_xray_setup] table."""

from __future__ import annotations

import pytest
from config_helpers import assert_config_error, base_config


@pytest.mark.parametrize(
    "content",
    [
        # panel_port is a string, not an integer
        base_config().replace("panel_port = 35353", 'panel_port = "35353"'),
        # panel_port is below the valid range
        base_config().replace("panel_port = 35353", "panel_port = 0"),
        # panel_port is above the valid range
        base_config().replace("panel_port = 35353", "panel_port = 65536"),
        # inbound_port is a string, not an integer
        base_config().replace("inbound_port = 443", 'inbound_port = "443"'),
        # inbound_port is below the valid range
        base_config().replace("inbound_port = 443", "inbound_port = 0"),
    ],
)
def test_three_x_ui_invalid_values_raise(
    tmp_path: pytest.TempPathFactory, content: str
) -> None:
    # A wrong type or an out-of-range port is a config error.
    assert_config_error(tmp_path, content, "three_x_ui_xray_setup")
