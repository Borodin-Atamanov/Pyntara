"""Config tests for [yggdrasil_service_setup]."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import (
    assert_config_error,
    base_config,
)


@pytest.mark.parametrize(
    "content",
    [
        # yggdrasil github_repo is a number, not a string
        base_config().replace(
            'github_repo = "yggdrasil-network/yggdrasil-go"', "github_repo = 1"
        ),
        # yggdrasil github_repo is an empty string
        base_config().replace(
            'github_repo = "yggdrasil-network/yggdrasil-go"', 'github_repo = ""'
        ),
        # yggdrasil download_dir is a number, not a string
        base_config().replace(
            'download_dir = "/var/lib/pyntara/yggdrasil-download"',
            "download_dir = 1",
        ),
        # yggdrasil download_dir is an empty string
        base_config().replace(
            'download_dir = "/var/lib/pyntara/yggdrasil-download"',
            'download_dir = ""',
        ),
        # yggdrasil service_unit_name is a number, not a string
        base_config().replace(
            'service_unit_name = "yggdrasil.service"', "service_unit_name = 1"
        ),
        # yggdrasil service_unit_name is an empty string
        base_config().replace(
            'service_unit_name = "yggdrasil.service"', 'service_unit_name = ""'
        ),
        # yggdrasil install_retries is a string, not an integer
        base_config().replace(
            'service_unit_name = "yggdrasil.service"\ninstall_retries = 3',
            'service_unit_name = "yggdrasil.service"\ninstall_retries = "3"',
        ),
        # yggdrasil install_retries is zero
        base_config().replace(
            'service_unit_name = "yggdrasil.service"\ninstall_retries = 3',
            'service_unit_name = "yggdrasil.service"\ninstall_retries = 0',
        ),
        # yggdrasil config_path is a number, not a string
        base_config().replace(
            'config_path = "/etc/yggdrasil/yggdrasil.conf"', "config_path = 1"
        ),
        # yggdrasil private_key_path is an empty string
        base_config().replace(
            'private_key_path = "/etc/yggdrasil/private-key.pem"',
            'private_key_path = ""',
        ),
        # yggdrasil config_file_mode is not an octal string
        base_config().replace('config_file_mode = "0640"', 'config_file_mode = "640"'),
        # yggdrasil private_key_file_mode is not octal
        base_config().replace(
            'private_key_file_mode = "0600"', 'private_key_file_mode = "zzzz"'
        ),
        # yggdrasil if_name is a number, not a string
        base_config().replace('if_name = "ygg"', "if_name = 1"),
        # yggdrasil if_mtu is below the yggdrasil range
        base_config().replace("if_mtu = 65535", "if_mtu = 1000"),
        # yggdrasil if_mtu is above the yggdrasil range
        base_config().replace("if_mtu = 65535", "if_mtu = 70000"),
        # yggdrasil if_mtu is a string, not an integer
        base_config().replace("if_mtu = 65535", 'if_mtu = "65535"'),
        # yggdrasil admin_listen is an empty string
        base_config().replace(
            'admin_listen = "unix:///var/run/yggdrasil/yggdrasil.sock"',
            'admin_listen = ""',
        ),
        # yggdrasil listen is a string, not an array
        base_config().replace(
            'listen = ["tcp://[::]:0", "tls://[::]:0"]', 'listen = "tcp://[::]:0"'
        ),
        # yggdrasil listen contains a wss scheme, which is not a listener
        base_config().replace(
            'listen = ["tcp://[::]:0", "tls://[::]:0"]',
            'listen = ["wss://[::]:0"]',
        ),
        # yggdrasil listen contains a socks scheme, which is outgoing only
        base_config().replace(
            'listen = ["tcp://[::]:0", "tls://[::]:0"]',
            'listen = ["socks://proxy:1080/1.2.3.4:1000"]',
        ),
        # yggdrasil multicast_interfaces is a string, not an array
        base_config().replace(
            '[[yggdrasil_service_setup.multicast_interfaces]]\nregex = ".*"\nbeacon = true\nlisten = true\n',
            'multicast_interfaces = ".*"\n',
        ),
        # yggdrasil multicast regex is an empty string
        base_config().replace('regex = ".*"', 'regex = ""'),
        # yggdrasil multicast beacon is a string, not a boolean
        base_config().replace("beacon = true", 'beacon = "true"'),
        # yggdrasil multicast listen is an integer, not a boolean
        base_config().replace("listen = true", "listen = 1"),
        # yggdrasil peer_batch_size is zero
        base_config().replace("peer_batch_size = 100", "peer_batch_size = 0"),
        # yggdrasil peer_target_count is a string, not an integer
        base_config().replace("peer_target_count = 6", 'peer_target_count = "6"'),
        # yggdrasil peer_probe_timeout_seconds is zero
        base_config().replace(
            "peer_probe_timeout_seconds = 30", "peer_probe_timeout_seconds = 0"
        ),
        # yggdrasil peer_max_batches is negative
        base_config().replace("peer_max_batches = 0", "peer_max_batches = -1"),
        # yggdrasil static_peers is a string, not an array
        base_config().replace(
            "static_peers = []", 'static_peers = "tcp://1.2.3.4:1000"'
        ),
        # yggdrasil static_peers contains an unknown scheme
        base_config().replace(
            "static_peers = []", 'static_peers = ["carrierpigeon://1.2.3.4:1000"]'
        ),
        # yggdrasil address_save_retry_base_seconds is a string, not an integer
        base_config().replace(
            "address_save_retry_base_seconds = 1",
            'address_save_retry_base_seconds = "1"',
        ),
        # yggdrasil address_save_retry_base_seconds is zero
        base_config().replace(
            "address_save_retry_base_seconds = 1",
            "address_save_retry_base_seconds = 0",
        ),
        # yggdrasil address_save_retry_multiplier is one
        base_config().replace(
            "address_save_retry_multiplier = 2",
            "address_save_retry_multiplier = 1",
        ),
        # yggdrasil address_save_retry_max_seconds is a string, not an integer
        base_config().replace(
            "address_save_retry_max_seconds = 67",
            'address_save_retry_max_seconds = "67"',
        ),
        # yggdrasil address_save_retry_max_seconds is below the base
        base_config().replace(
            "address_save_retry_max_seconds = 67",
            "address_save_retry_max_seconds = 0",
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)
