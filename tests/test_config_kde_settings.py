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
        # numlock_on_boot is a number, not a string
        base_config().replace(
            'numlock_on_boot = "off"', "numlock_on_boot = 1"
        ),
        # numlock_on_boot is outside the vocabulary
        base_config().replace(
            'numlock_on_boot = "off"', 'numlock_on_boot = "sometimes"'
        ),
        # touchpad_click_method is outside the vocabulary
        base_config().replace(
            'touchpad_click_method = "clickfinger"', 'touchpad_click_method = "tap"'
        ),
        # touchpad_disable_on_external_mouse is a string, not a boolean
        base_config().replace(
            "touchpad_disable_on_external_mouse = false",
            'touchpad_disable_on_external_mouse = "false"',
        ),
        # virtual_keyboard_enabled is a string, not a boolean
        base_config().replace(
            "virtual_keyboard_enabled = true",
            'virtual_keyboard_enabled = "true"',
        ),
        # virtual_keyboard_input_method is an empty string
        base_config().replace(
            'virtual_keyboard_input_method = "/usr/share/applications/org.kde.plasma.keyboard.desktop"',
            'virtual_keyboard_input_method = ""',
        ),
        # virtual_keyboard_locales is a string, not an array
        base_config().replace(
            'virtual_keyboard_locales = ["en_US", "es_MX", "ru_RU"]',
            'virtual_keyboard_locales = "en_US"',
        ),
        # kwin_reload_command is an empty array
        base_config().replace(
            'kwin_reload_command = ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"]',
            "kwin_reload_command = []",
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
    assert config.kde_settings.numlock_on_boot == "off"
    assert config.kde_settings.touchpad_click_method == "clickfinger"
    assert config.kde_settings.touchpad_disable_on_external_mouse is False
    assert config.kde_settings.virtual_keyboard_enabled is True
    assert config.kde_settings.virtual_keyboard_input_method == (
        "/usr/share/applications/org.kde.plasma.keyboard.desktop"
    )
    assert config.kde_settings.virtual_keyboard_locales == ("en_US", "es_MX", "ru_RU")
    assert config.kde_settings.kwin_reload_command == (
        "qdbus6",
        "org.kde.KWin",
        "/KWin",
        "org.kde.KWin.reconfigure",
    )
