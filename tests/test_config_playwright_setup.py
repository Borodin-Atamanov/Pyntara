"""Config tests for the [playwright_setup] table."""

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
        # packages is a string, not an array
        base_config().replace(
            'packages = ["nodejs", "npm"]', 'packages = "nodejs"'
        ),
        # packages contains a number, not strings
        base_config().replace('packages = ["nodejs", "npm"]', "packages = [1]"),
        # username is a number, not a string
        base_config().replace(
            "[playwright_setup]\nusername = \"i\"",
            "[playwright_setup]\nusername = 42",
        ),
        # home_dir is a number, not a string
        base_config().replace(
            'home_dir = "/home/i"\npackages', "home_dir = 42\npackages"
        ),
        # cli_package is a number, not a string
        base_config().replace(
            'cli_package = "@playwright/cli"', "cli_package = 42"
        ),
        # package_status_timeout_seconds of this section is a string
        base_config().replace(
            'packages = ["nodejs", "npm"]\n'
            "package_status_timeout_seconds = 30\n",
            'packages = ["nodejs", "npm"]\n'
            'package_status_timeout_seconds = "30"\n',
        ),
        # package_install_retries of this section is a string
        base_config().replace(
            "package_status_timeout_seconds = 30\n"
            "package_install_retries = 3\n"
            'cli_package = "@playwright/cli"',
            "package_status_timeout_seconds = 30\n"
            'package_install_retries = "3"\n'
            'cli_package = "@playwright/cli"',
        ),
        # npm_install_timeout_seconds is a string
        base_config().replace(
            "npm_install_timeout_seconds = 900",
            'npm_install_timeout_seconds = "900"',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_playwright_values(tmp_path: Path) -> None:
    # The typed values round-trip from the config document.
    config = load_checked_config(write_config(tmp_path, base_config()))
    assert config.playwright_setup.username == "i"
    assert config.playwright_setup.home_dir == "/home/i"
    assert config.playwright_setup.packages == ("nodejs", "npm")
    assert config.playwright_setup.package_status_timeout_seconds == 30
    assert config.playwright_setup.package_install_retries == 3
    assert config.playwright_setup.cli_package == "@playwright/cli"
    assert config.playwright_setup.npm_install_timeout_seconds == 900
