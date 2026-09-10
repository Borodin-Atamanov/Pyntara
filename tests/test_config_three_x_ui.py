"""Config tests for the [three_x_ui_xray_setup] table."""

from __future__ import annotations

from pathlib import Path

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
        # ssl_enabled is a string, not a boolean
        base_config().replace("ssl_enabled = true", 'ssl_enabled = "true"'),
        # ssl_enabled is an integer, not a boolean
        base_config().replace("ssl_enabled = true", "ssl_enabled = 1"),
        # inbound_port is a string, not an integer
        base_config().replace("inbound_port = 443", 'inbound_port = "443"'),
        # inbound_port is below the valid range
        base_config().replace("inbound_port = 443", "inbound_port = 0"),
        # acme_port is a string, not an integer
        base_config().replace("acme_port = 80", 'acme_port = "80"'),
        # acme_port is below the valid range
        base_config().replace("acme_port = 80", "acme_port = 0"),
        # acme_port is above the valid range
        base_config().replace("acme_port = 80", "acme_port = 65536"),
        # cert_dir is empty
        base_config().replace('cert_dir = "/root/cert/ip"', 'cert_dir = ""'),
        # self_signed_cert_dir is empty
        base_config().replace(
            'self_signed_cert_dir = "/root/cert/selfsigned"',
            'self_signed_cert_dir = ""',
        ),
        # share_addr_strategy is not one of the panel strategies
        base_config().replace(
            'share_addr_strategy = "custom"', 'share_addr_strategy = "random"'
        ),
        # reality_fingerprint is empty
        base_config().replace(
            'reality_fingerprint = "chrome"', 'reality_fingerprint = ""'
        ),
        # subscription_path has no leading slash
        base_config().replace(
            'subscription_path = "/s/"', 'subscription_path = "s/"'
        ),
        # subscription_path has no trailing slash
        base_config().replace(
            'subscription_path = "/s/"', 'subscription_path = "/s"'
        ),
        # subscription_path is empty
        base_config().replace('subscription_path = "/s/"', 'subscription_path = ""'),
        # subscription_path is a bare slash
        base_config().replace('subscription_path = "/s/"', 'subscription_path = "/"'),
        # subscription_json_path is an integer, not a string
        base_config().replace(
            'subscription_json_path = "/j/"', "subscription_json_path = 1"
        ),
        # server_ip_services is empty
        base_config().replace(
            'server_ip_services = ["https://api4.ipify.org", "https://ipv4.icanhazip.com", "https://v4.api.ipinfo.io/ip", "https://ipv4.myexternalip.com/raw", "https://4.ident.me", "https://check-host.net/ip"]',
            "server_ip_services = []",
        ),
    ],
)
def test_three_x_ui_invalid_values_raise(
    tmp_path: Path, content: str
) -> None:
    # A wrong type or an out-of-range port is a config error.
    assert_config_error(tmp_path, content, "three_x_ui_xray_setup")
