"""Config tests for [swapfile_service_install], [zswap_service] and
[zram_service]."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config


@pytest.mark.parametrize(
    "content",
    [
        # swapfile_path is a number, not a string
        base_config().replace('swapfile_path = "/swapfile"', "swapfile_path = 1"),
        # ram_multiplier is a string, not a number
        base_config().replace("ram_multiplier = 2", 'ram_multiplier = "2"'),
        # ram_extra_mb is a string, not an integer
        base_config().replace("ram_extra_mb = 4096", 'ram_extra_mb = "4096"'),
        # disk_fraction is above one
        base_config().replace("disk_fraction = 0.5", "disk_fraction = 1.5"),
        # disk_fraction is zero
        base_config().replace("disk_fraction = 0.5", "disk_fraction = 0"),
        # swapfile_mode is not a four-digit octal string
        base_config().replace('swapfile_mode = "0600"', 'swapfile_mode = "600"'),
        # swapfile_mode is not octal
        base_config().replace('swapfile_mode = "0600"', 'swapfile_mode = "zzzz"'),
        # swapfile_mode is a number, not a string
        base_config().replace('swapfile_mode = "0600"', "swapfile_mode = 600"),
        # size_tolerance_mb is a string, not an integer
        base_config().replace("size_tolerance_mb = 1", 'size_tolerance_mb = "1"'),
        # size_tolerance_mb is negative
        base_config().replace("size_tolerance_mb = 1", "size_tolerance_mb = -1"),
        # zswap enabled is a string, not a boolean
        base_config().replace("enabled = true", 'enabled = "true"'),
        # zswap compressor is a number, not a string
        base_config().replace('compressor = "zstd"', "compressor = 1"),
        # zswap compressor is an empty string
        base_config().replace('compressor = "zstd"', 'compressor = ""'),
        # zswap max_pool_percent is a string, not an integer
        base_config().replace("max_pool_percent = 50", 'max_pool_percent = "50"'),
        # zswap max_pool_percent is zero
        base_config().replace("max_pool_percent = 50", "max_pool_percent = 0"),
        # zswap max_pool_percent is above 100
        base_config().replace("max_pool_percent = 50", "max_pool_percent = 101"),
        # zswap accept_threshold_percent is below one
        base_config().replace(
            "accept_threshold_percent = 100", "accept_threshold_percent = 0"
        ),
        # zswap accept_threshold_percent is above 100
        base_config().replace(
            "accept_threshold_percent = 100", "accept_threshold_percent = 101"
        ),
        # zswap shrinker_enabled is an integer, not a boolean
        base_config().replace("shrinker_enabled = true", "shrinker_enabled = 1"),
        # swapfile service_unit_name is a number, not a string
        base_config().replace(
            'service_unit_name = "swapfile.service"', "service_unit_name = 1"
        ),
        # swapfile service_unit_name is an empty string
        base_config().replace(
            'service_unit_name = "swapfile.service"', 'service_unit_name = ""'
        ),
        # zswap service_unit_name is a number, not a string
        base_config().replace(
            'service_unit_name = "zswap.service"', "service_unit_name = 1"
        ),
        # zram service_unit_name is an empty string
        base_config().replace(
            'service_unit_name = "zram.service"', 'service_unit_name = ""'
        ),
        # zram reset_busy_attempts is zero
        base_config().replace(
            "reset_busy_attempts = 5", "reset_busy_attempts = 0"
        ),
        # zram reset_busy_retry_delay_seconds is a string, not a number
        base_config().replace(
            "reset_busy_retry_delay_seconds = 0.5",
            'reset_busy_retry_delay_seconds = "0.5"',
        ),
        # zram reset_busy_retry_delay_seconds is zero
        base_config().replace(
            "reset_busy_retry_delay_seconds = 0.5",
            "reset_busy_retry_delay_seconds = 0",
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)
