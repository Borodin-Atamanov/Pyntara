"""Unit tests for config.toml loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara.config import ConfigError, load_config

VALID_TOML = """\
[engine]
task_data_root = "/var/lib/pyntara/task-data"
notice_timeout = 7
command_timeout_seconds = 1800
process_check_timeout_seconds = 5

[cli_tools]
packages = ["mc", "htop"]
package_status_timeout_seconds = 30
package_install_retries = 3
package_success_threshold_percent = 70
"""


def test_load_config_returns_typed_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(VALID_TOML, encoding="utf-8")
    config = load_config(config_path)
    assert config.engine.task_data_root == Path("/var/lib/pyntara/task-data")
    assert config.engine.notice_timeout == 7
    assert config.engine.command_timeout_seconds == 1800
    assert config.engine.process_check_timeout_seconds == 5
    assert config.cli_tools.packages == ("mc", "htop")
    assert config.cli_tools.package_status_timeout_seconds == 30
    assert config.cli_tools.package_install_retries == 3
    assert config.cli_tools.package_success_threshold_percent == 70


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")


def test_load_config_invalid_toml_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[engine\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(config_path)


def test_load_config_missing_section_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[engine]\nnotice_timeout = 7\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)


# Valid base config; each wrong-type case replaces exactly one value.
_BASE_CONFIG = (
    '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
    "command_timeout_seconds = 1800\nprocess_check_timeout_seconds = 5\n"
    '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\n'
    "package_install_retries = 3\npackage_success_threshold_percent = 70\n"
)


@pytest.mark.parametrize(
    "content",
    [
        # notice_timeout is a string, not an integer
        _BASE_CONFIG.replace('notice_timeout = 7', 'notice_timeout = "7"'),
        # packages is a string, not an array
        _BASE_CONFIG.replace('packages = ["mc"]', 'packages = "mc"'),
        # packages contains a number, not strings
        _BASE_CONFIG.replace('packages = ["mc"]', "packages = [1, 2]"),
        # task_data_root is a number, not a string
        _BASE_CONFIG.replace('task_data_root = "/tmp"', "task_data_root = 42"),
        # command_timeout_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "command_timeout_seconds = 1800", 'command_timeout_seconds = "1800"'
        ),
        # process_check_timeout_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "process_check_timeout_seconds = 5", 'process_check_timeout_seconds = "5"'
        ),
        # package_status_timeout_seconds is a string, not an integer
        _BASE_CONFIG.replace(
            "package_status_timeout_seconds = 30", 'package_status_timeout_seconds = "30"'
        ),
        # package_install_retries is a string, not an integer
        _BASE_CONFIG.replace(
            "package_install_retries = 3", 'package_install_retries = "3"'
        ),
        # package_success_threshold_percent is a string, not an integer
        _BASE_CONFIG.replace(
            "package_success_threshold_percent = 70",
            'package_success_threshold_percent = "70"',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)


@pytest.mark.parametrize(
    "content",
    [
        # threshold above 100 is invalid
        _BASE_CONFIG.replace(
            "package_success_threshold_percent = 70",
            "package_success_threshold_percent = 101",
        ),
        # threshold below 0 is invalid
        _BASE_CONFIG.replace(
            "package_success_threshold_percent = 70",
            "package_success_threshold_percent = -1",
        ),
    ],
)
def test_load_config_threshold_out_of_range_raises(
    tmp_path: Path, content: str
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match="between 0 and 100"):
        load_config(config_path)


def test_load_config_bool_not_accepted_as_timeout(tmp_path: Path) -> None:
    # TOML booleans parse as Python bool, which is a subclass of int and must
    # not be accepted as a countdown value.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = true\n'
        'command_timeout_seconds = 1800\nprocess_check_timeout_seconds = 5\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\npackage_install_retries = 3\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_bool_not_accepted_as_retries(tmp_path: Path) -> None:
    # A bool value for package_install_retries must be rejected too.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[engine]\ntask_data_root = "/tmp"\nnotice_timeout = 7\n'
        'command_timeout_seconds = 1800\nprocess_check_timeout_seconds = 5\n'
        '[cli_tools]\npackages = ["mc"]\npackage_status_timeout_seconds = 30\npackage_install_retries = true\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)
