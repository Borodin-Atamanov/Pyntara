"""Config tests for the [kde_settings] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


@pytest.mark.parametrize(
    "content",
    [
        # packages is a string, not an array
        base_config().replace(
            'packages = ["plasma-workspace", "libkf6config-bin"]',
            'packages = "plasma-workspace"',
        ),
        # packages is an empty array
        base_config().replace(
            'packages = ["plasma-workspace", "libkf6config-bin"]', "packages = []"
        ),
        # packages contains a number, not strings
        base_config().replace(
            'packages = ["plasma-workspace", "libkf6config-bin"]', "packages = [1]"
        ),
        # username is a number, not a string
        base_config().replace('username = "i"', "username = 42"),
        # username is an empty string
        base_config().replace('username = "i"', 'username = ""'),
        # home_dir is a number, not a string
        base_config().replace('home_dir = "/home/i"', "home_dir = 42"),
        # color_scheme is an empty string
        base_config().replace(
            'color_scheme = "BreezeDark"', 'color_scheme = ""'
        ),
        # look_and_feel is a number, not a string
        base_config().replace(
            'look_and_feel = "org.kubuntudark.desktop"', "look_and_feel = 1"
        ),
        # look_and_feel is an empty string
        base_config().replace(
            'look_and_feel = "org.kubuntudark.desktop"', 'look_and_feel = ""'
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_kde_settings_values(tmp_path: Path) -> None:
    # The typed values round-trip from the config document.
    config = load_config(write_config(tmp_path, base_config()))
    assert config.kde_settings.packages == ("plasma-workspace", "libkf6config-bin")
    assert config.kde_settings.username == "i"
    assert config.kde_settings.home_dir == "/home/i"
    assert config.kde_settings.color_scheme == "BreezeDark"
    assert config.kde_settings.look_and_feel == "org.kubuntudark.desktop"
