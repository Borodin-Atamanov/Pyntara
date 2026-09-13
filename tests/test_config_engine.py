"""Config tests for [engine], [cli_tools] and [add_extra_repos]."""

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
        # notice_timeout is a string, not an integer
        base_config().replace('notice_timeout = 7', 'notice_timeout = "7"'),
        # packages is a string, not an array
        base_config().replace('packages = ["mc"]', 'packages = "mc"'),
        # packages contains a number, not strings
        base_config().replace('packages = ["mc"]', "packages = [1, 2]"),
        # task_data_root is a number, not a string
        base_config().replace('task_data_root = "/tmp"', "task_data_root = 42"),
        # systemd_unit_dir is a number, not a string
        base_config().replace(
            'systemd_unit_dir = "/etc/systemd/system"', "systemd_unit_dir = 42"
        ),
        # command_timeout_seconds is a string, not an integer
        base_config().replace(
            "command_timeout_seconds = 8000", 'command_timeout_seconds = "8000"'
        ),
        # curl_timeout_seconds is a string, not an integer
        base_config().replace(
            "curl_timeout_seconds = 777", 'curl_timeout_seconds = "777"'
        ),
        # curl_timeout_seconds is zero
        base_config().replace("curl_timeout_seconds = 777", "curl_timeout_seconds = 0"),
        # curl_download_timeout_seconds is a string, not an integer
        base_config().replace(
            "curl_download_timeout_seconds = 7777",
            'curl_download_timeout_seconds = "7777"',
        ),
        # curl_download_timeout_seconds is zero
        base_config().replace(
            "curl_download_timeout_seconds = 7777",
            "curl_download_timeout_seconds = 0",
        ),
        # curl_retries is a string, not an integer
        base_config().replace("curl_retries = 17", 'curl_retries = "17"'),
        # curl_retries is negative
        base_config().replace("curl_retries = 17", "curl_retries = -1"),
        # curl_retry_delay_seconds is a string, not an integer
        base_config().replace(
            "curl_retry_delay_seconds = 3", 'curl_retry_delay_seconds = "3"'
        ),
        # curl_retry_delay_seconds is zero
        base_config().replace(
            "curl_retry_delay_seconds = 3", "curl_retry_delay_seconds = 0"
        ),
        # curl_connect_timeout_seconds is a string, not an integer
        base_config().replace(
            "curl_connect_timeout_seconds = 60",
            'curl_connect_timeout_seconds = "60"',
        ),
        # curl_connect_timeout_seconds is zero
        base_config().replace(
            "curl_connect_timeout_seconds = 60", "curl_connect_timeout_seconds = 0"
        ),
        # curl_retry_max_time_seconds is a string, not an integer
        base_config().replace(
            "curl_retry_max_time_seconds = 7777",
            'curl_retry_max_time_seconds = "7777"',
        ),
        # curl_retry_max_time_seconds is zero
        base_config().replace(
            "curl_retry_max_time_seconds = 7777", "curl_retry_max_time_seconds = 0"
        ),
        # process_check_timeout_seconds is a string, not an integer
        base_config().replace(
            "process_check_timeout_seconds = 5", 'process_check_timeout_seconds = "5"'
        ),
        # task_start_delay_seconds is a string, not a number
        base_config().replace(
            "task_start_delay_seconds = 0.5", 'task_start_delay_seconds = "0.5"'
        ),
        # journal_identifier is a number, not a string
        base_config().replace(
            'journal_identifier = "pyntara-engine"', "journal_identifier = 42"
        ),
        # journal_identifier is empty
        base_config().replace(
            'journal_identifier = "pyntara-engine"', 'journal_identifier = ""'
        ),
        # release_asset_architectures is a string, not a table
        base_config().replace(
            'release_asset_architectures = { amd64 = "x86_64", arm64 = "aarch64" }',
            'release_asset_architectures = "amd64"',
        ),
        # release_asset_architectures maps to an empty string
        base_config().replace(
            'release_asset_architectures = { amd64 = "x86_64", arm64 = "aarch64" }',
            'release_asset_architectures = { amd64 = "" }',
        ),
        # partial_download_file_suffix is an empty string
        base_config().replace(
            'partial_download_file_suffix = ".download"',
            'partial_download_file_suffix = ""',
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
        # session_environment_command is a string, not an array
        base_config().replace(
            'session_environment_command = ["systemctl", "--machine", "{username}@.host", "--user", "show-environment"]',
            'session_environment_command = "systemctl"',
        ),
        # session_environment_command contains an empty string
        base_config().replace(
            'session_environment_command = ["systemctl", "--machine", "{username}@.host", "--user", "show-environment"]',
            'session_environment_command = [""]',
        ),
        # session_environment_keys contains a number, not strings
        base_config().replace(
            'session_environment_keys = ["DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR"]',
            'session_environment_keys = [1]',
        ),
        # session_bus_key is an empty string
        base_config().replace(
            'session_bus_key = "DBUS_SESSION_BUS_ADDRESS"', 'session_bus_key = ""'
        ),
        # session_display_keys is a string, not an array
        base_config().replace(
            'session_display_keys = ["WAYLAND_DISPLAY", "DISPLAY"]',
            'session_display_keys = "WAYLAND_DISPLAY"',
        ),
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
    config = load_checked_config(
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
    config = load_checked_config(write_config(tmp_path, base_config()))
    assert config.add_extra_repos.keep_downloaded_debs is True


def test_load_config_requires_keep_downloaded_debs(tmp_path: Path) -> None:
    # The apt retention setting is explicit: a missing key is rejected, not
    # silently defaulted.
    content = base_config().replace("keep_downloaded_debs = true\n", "")
    assert_config_error(tmp_path, content, match="must be a boolean")


def test_load_config_parallel_query_values(tmp_path: Path) -> None:
    # The parallel query, its write-out text and its source marker
    # round-trip, and the marker is the token inside the text, so the
    # parser and the query can never disagree.
    config = load_checked_config(write_config(tmp_path, base_config()))
    engine = config.engine
    assert "--parallel" in engine.curl_parallel_command
    assert engine.curl_parallel_source_marker in engine.curl_parallel_write_out
    assert engine.curl_parallel_write_out.endswith("\n")


def test_load_config_parallel_marker_outside_the_text_raises(
    tmp_path: Path,
) -> None:
    # A marker the write-out text does not print would leave every answer
    # unattributed, so the checks refuse it.
    content = base_config().replace(
        'curl_parallel_source_marker = "@@pyntara-source@@"',
        'curl_parallel_source_marker = "@@other@@"',
    )
    assert_config_error(tmp_path, content)


def test_load_config_os_release_vocabulary(tmp_path: Path) -> None:
    # The fields of the distribution identity file and the values that mean
    # a Debian-based system round-trip from the shared document.
    config = load_checked_config(write_config(tmp_path, base_config()))
    assert config.engine.os_release_family_keys == ("ID", "ID_LIKE")
    assert config.engine.os_release_debian_family_names == ("debian", "ubuntu")


def test_load_config_curl_command_templates(tmp_path: Path) -> None:
    # The two command templates and the write-out text round-trip, and the
    # download template carries the placeholders the shared helper fills.
    config = load_checked_config(write_config(tmp_path, base_config()))
    engine = config.engine
    assert engine.curl_download_command[0] == "curl"
    assert "{output_path}" in engine.curl_download_command
    assert "{write_out}" in engine.curl_download_command
    assert engine.curl_query_command == (
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
    )
    assert engine.curl_download_write_out == "took %{time_total}s"


@pytest.mark.parametrize(
    "content",
    [
        # curl_download_command carries no {output_path}
        base_config().replace(
            '"--output", "{output_path}"', '"--output", "archive.bin"'
        ),
        # curl_download_command carries no {write_out}
        base_config().replace('"--write-out", "{write_out}"', '"--write-out", "x"'),
        # curl_query_command is empty
        base_config().replace(
            'curl_query_command = ["curl", "--fail", "--silent", "--show-error", "--location"]',
            "curl_query_command = []",
        ),
        # curl_download_write_out is empty
        base_config().replace(
            'curl_download_write_out = "took %{time_total}s"',
            'curl_download_write_out = ""',
        ),
    ],
)
def test_load_config_wrong_curl_commands_raise(
    tmp_path: Path, content: str
) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_bool_not_accepted_as_timeout(tmp_path: Path) -> None:
    # TOML booleans parse as Python bool, which is a subclass of int and must
    # not be accepted as a countdown value.
    assert_config_error(
        tmp_path,
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = true\n'
        'command_timeout_seconds = 1800\nerror_priority = 3\nprogress_priority = 7\nprocess_check_timeout_seconds = 5\n'
        'process_check_command = ["pgrep", "-x", "{process_name}"]\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\npackage_install_retries = 3\n',
    )


def test_load_config_bool_not_accepted_as_retries(tmp_path: Path) -> None:
    # A bool value for package_install_retries must be rejected too.
    assert_config_error(
        tmp_path,
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
        'command_timeout_seconds = 1800\nerror_priority = 3\nprogress_priority = 7\nprocess_check_timeout_seconds = 5\n'
        'process_check_command = ["pgrep", "-x", "{process_name}"]\n'
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
