"""Config tests for [engine], [cli_tools] and [add_extra_repos]."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


@pytest.mark.parametrize(
    "content",
    [
        # notice_timeout is a string, not an integer
        base_config().replace('notice_timeout = 7', 'notice_timeout = "7"'),
        # packages is a string, not an array
        base_config().replace('packages = ["mc"]', 'packages = "mc"'),
        # packages contains a number, not strings
        base_config().replace('packages = ["mc"]', "packages = [1, 2]"),
        # task_data_root is a number, not a string
        base_config().replace('task_data_root = "/tmp"', "task_data_root = 42"),
        # command_timeout_seconds is a string, not an integer
        base_config().replace(
            "command_timeout_seconds = 1800", 'command_timeout_seconds = "1800"'
        ),
        # curl_timeout_seconds is a string, not an integer
        base_config().replace(
            "curl_timeout_seconds = 777", 'curl_timeout_seconds = "777"'
        ),
        # curl_timeout_seconds is zero
        base_config().replace("curl_timeout_seconds = 777", "curl_timeout_seconds = 0"),
        # curl_retries is a string, not an integer
        base_config().replace("curl_retries = 13", 'curl_retries = "13"'),
        # curl_retries is negative
        base_config().replace("curl_retries = 13", "curl_retries = -1"),
        # curl_connect_timeout_seconds is a string, not an integer
        base_config().replace(
            "curl_connect_timeout_seconds = 30",
            'curl_connect_timeout_seconds = "30"',
        ),
        # curl_connect_timeout_seconds is zero
        base_config().replace(
            "curl_connect_timeout_seconds = 30", "curl_connect_timeout_seconds = 0"
        ),
        # curl_retry_max_time_seconds is a string, not an integer
        base_config().replace(
            "curl_retry_max_time_seconds = 1500",
            'curl_retry_max_time_seconds = "1500"',
        ),
        # curl_retry_max_time_seconds is zero
        base_config().replace(
            "curl_retry_max_time_seconds = 1500", "curl_retry_max_time_seconds = 0"
        ),
        # process_check_timeout_seconds is a string, not an integer
        base_config().replace(
            "process_check_timeout_seconds = 5", 'process_check_timeout_seconds = "5"'
        ),
        # task_start_delay_seconds is a string, not a number
        base_config().replace(
            "task_start_delay_seconds = 0.5", 'task_start_delay_seconds = "0.5"'
        ),
        # desktop_detect_processes is a string, not an array
        base_config().replace(
            'desktop_detect_processes = ["kwin_wayland", "plasmashell"]',
            'desktop_detect_processes = "kwin_wayland"',
        ),
        # desktop_detect_processes is an empty array
        base_config().replace(
            'desktop_detect_processes = ["kwin_wayland", "plasmashell"]',
            "desktop_detect_processes = []",
        ),
        # desktop_detect_processes contains a number, not strings
        base_config().replace(
            'desktop_detect_processes = ["kwin_wayland", "plasmashell"]',
            "desktop_detect_processes = [1]",
        ),
        # desktop_detect_processes contains an empty string
        base_config().replace(
            'desktop_detect_processes = ["kwin_wayland", "plasmashell"]',
            'desktop_detect_processes = [""]',
        ),
        # package_status_timeout_seconds is a string, not an integer
        base_config().replace(
            "package_status_timeout_seconds = 30", 'package_status_timeout_seconds = "30"'
        ),
        # package_install_retries is a string, not an integer
        base_config().replace(
            "package_install_retries = 3", 'package_install_retries = "3"'
        ),
        # package_success_threshold_percent is a string, not an integer
        base_config().replace(
            "package_success_threshold_percent = 70",
            'package_success_threshold_percent = "70"',
        ),
        # components is a string, not an array
        base_config().replace('components = ["universe"]', 'components = "universe"'),
        # components contains a number, not strings
        base_config().replace('components = ["universe"]', "components = [1]"),
        # components contains an empty string
        base_config().replace('components = ["universe"]', 'components = [""]'),
        # components contains whitespace
        base_config().replace('components = ["universe"]', 'components = ["universe "]'),
        # components is an empty array
        base_config().replace('components = ["universe"]', "components = []"),
        # keep_downloaded_debs is a string, not a boolean
        base_config().replace(
            "keep_downloaded_debs = true", 'keep_downloaded_debs = "true"'
        ),
        # keep_downloaded_debs is an integer, not a boolean
        base_config().replace("keep_downloaded_debs = true", "keep_downloaded_debs = 1"),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


@pytest.mark.parametrize(
    "content",
    [
        # threshold above 100 is invalid
        base_config().replace(
            "package_success_threshold_percent = 70",
            "package_success_threshold_percent = 101",
        ),
        # threshold below 0 is invalid
        base_config().replace(
            "package_success_threshold_percent = 70",
            "package_success_threshold_percent = -1",
        ),
    ],
)
def test_load_config_threshold_out_of_range_raises(
    tmp_path: Path, content: str
) -> None:
    assert_config_error(tmp_path, content, match="between 0 and 100")


def test_load_config_deduplicates_components(tmp_path: Path) -> None:
    # Duplicate components are removed while the configured order is kept.
    config = load_config(
        write_config(
            tmp_path,
            base_config().replace(
                'components = ["universe"]',
                'components = ["universe", "multiverse", "universe"]',
            ),
        )
    )
    assert config.add_extra_repos.components == ("universe", "multiverse")


def test_load_config_parses_keep_downloaded_debs(tmp_path: Path) -> None:
    # The configured apt retention flag reaches the parsed config.
    config = load_config(write_config(tmp_path, base_config()))
    assert config.add_extra_repos.keep_downloaded_debs is True


def test_load_config_requires_keep_downloaded_debs(tmp_path: Path) -> None:
    # The apt retention setting is explicit: a missing key is rejected, not
    # silently defaulted.
    content = base_config().replace("keep_downloaded_debs = true\n", "")
    assert_config_error(tmp_path, content, match="must be a boolean")


def test_load_config_bool_not_accepted_as_timeout(tmp_path: Path) -> None:
    # TOML booleans parse as Python bool, which is a subclass of int and must
    # not be accepted as a countdown value.
    assert_config_error(
        tmp_path,
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = true\n'
        'command_timeout_seconds = 1800\nerror_priority = 3\nprogress_priority = 7\nprocess_check_timeout_seconds = 5\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\npackage_install_retries = 3\n',
    )


def test_load_config_bool_not_accepted_as_retries(tmp_path: Path) -> None:
    # A bool value for package_install_retries must be rejected too.
    assert_config_error(
        tmp_path,
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
        'command_timeout_seconds = 1800\nerror_priority = 3\nprogress_priority = 7\nprocess_check_timeout_seconds = 5\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\npackage_install_retries = true\n',
    )


@pytest.mark.parametrize(
    "content",
    [
        # error_priority is a string, not an integer
        base_config().replace("error_priority = 3", 'error_priority = "3"'),
        # error_priority is above 7
        base_config().replace("error_priority = 3", "error_priority = 8"),
        # error_priority is below 0
        base_config().replace("error_priority = 3", "error_priority = -1"),
    ],
)
def test_load_config_error_priority_invalid_raises(
    tmp_path: Path, content: str
) -> None:
    assert_config_error(tmp_path, content, match="error_priority")


@pytest.mark.parametrize(
    "content",
    [
        # progress_priority is a string, not an integer
        base_config().replace("progress_priority = 7", 'progress_priority = "7"'),
        # progress_priority is above 7
        base_config().replace("progress_priority = 7", "progress_priority = 8"),
        # progress_priority is below 0
        base_config().replace("progress_priority = 7", "progress_priority = -1"),
    ],
)
def test_load_config_progress_priority_invalid_raises(
    tmp_path: Path, content: str
) -> None:
    assert_config_error(tmp_path, content, match="progress_priority")


@pytest.mark.parametrize(
    "content",
    [
        # cache_size_bytes is a string, not an integer
        base_config().replace(
            "cache_size_bytes = 16777216", 'cache_size_bytes = "16777216"'
        ),
        # cache_size_bytes is zero
        base_config().replace("cache_size_bytes = 16777216", "cache_size_bytes = 0"),
        # cache_size_bytes is negative
        base_config().replace("cache_size_bytes = 16777216", "cache_size_bytes = -1"),
    ],
)
def test_load_config_cache_size_bytes_invalid_raises(
    tmp_path: Path, content: str
) -> None:
    assert_config_error(tmp_path, content, match="cache_size_bytes")
