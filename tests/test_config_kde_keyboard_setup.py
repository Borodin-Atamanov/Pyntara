"""Config tests for the [kde_keyboard_setup] table."""

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
            'packages = ["libkf6config-bin", "qdbus-qt6", "python3-dbus"]',
            'packages = "libkf6config-bin"',
        ),
        # packages is an empty array
        base_config().replace(
            'packages = ["libkf6config-bin", "qdbus-qt6", "python3-dbus"]',
            "packages = []",
        ),
        # packages contains a number, not strings
        base_config().replace(
            'packages = ["libkf6config-bin", "qdbus-qt6", "python3-dbus"]',
            "packages = [1]",
        ),
        # username is a number, not a string
        base_config().replace('username = "i"', "username = 42"),
        # username is an empty string
        base_config().replace('username = "i"', 'username = ""'),
        # home_dir is an empty string
        base_config().replace('home_dir = "/home/i"', 'home_dir = ""'),
        # config_dir is a number, not a string
        base_config().replace('config_dir = "/home/i/.config"', "config_dir = 42"),
        # kxkbrc_file_name is an empty string
        base_config().replace('kxkbrc_file_name = "kxkbrc"', 'kxkbrc_file_name = ""'),
        # appletsrc_file_name is an empty string
        base_config().replace(
            'appletsrc_file_name = "plasma-org.kde.plasma.desktop-appletsrc"',
            'appletsrc_file_name = ""',
        ),
        # applet_plugin is a number, not a string
        base_config().replace(
            'applet_plugin = "org.kde.plasma.keyboardlayout"', "applet_plugin = 1"
        ),
        # layouts is a string, not an array
        base_config().replace('layouts = ["us", "ru", "es"]', 'layouts = "us"'),
        # layouts is an empty array
        base_config().replace('layouts = ["us", "ru", "es"]', "layouts = []"),
        # layouts contains a number, not strings
        base_config().replace('layouts = ["us", "ru", "es"]', "layouts = [1]"),
        # switch_option is an empty string
        base_config().replace(
            'switch_option = "grp:caps_select"', 'switch_option = ""'
        ),
        # reset_old_options is a string, not a boolean
        base_config().replace(
            "reset_old_options = true", 'reset_old_options = "true"'
        ),
        # switch_mode is an empty string
        base_config().replace('switch_mode = "WinClass"', 'switch_mode = ""'),
        # use_layout_switching is a string, not a boolean
        base_config().replace(
            "use_layout_switching = true", 'use_layout_switching = "true"'
        ),
        # indicator_display_style is an empty string
        base_config().replace(
            'indicator_display_style = "Flag"', 'indicator_display_style = ""'
        ),
        # kwin_reload_command is an empty array
        base_config().replace(
            'kwin_reload_command = ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"]',
            "kwin_reload_command = []",
        ),
        # panel_restart_command is a string, not an array
        base_config().replace(
            'panel_restart_command = ["systemctl", "--user", "--machine", "i@.host", "restart", "plasma-plasmashell.service"]',
            'panel_restart_command = "systemctl"',
        ),
        # layout_switch_shortcuts is an array, not a table
        base_config().replace(
            'layout_switch_shortcuts = { "Switch keyboard layout to Spanish" = "Meta+Q" }',
            'layout_switch_shortcuts = ["Meta+Q"]',
        ),
        # layout_switch_shortcuts key is an empty string
        base_config().replace(
            'layout_switch_shortcuts = { "Switch keyboard layout to Spanish" = "Meta+Q" }',
            'layout_switch_shortcuts = { "" = "Meta+Q" }',
        ),
        # layout_switch_shortcuts value is a number, not a string
        base_config().replace(
            'layout_switch_shortcuts = { "Switch keyboard layout to Spanish" = "Meta+Q" }',
            'layout_switch_shortcuts = { "Switch keyboard layout to Spanish" = 42 }',
        ),
        # layout_switch_shortcuts value is an empty string
        base_config().replace(
            'layout_switch_shortcuts = { "Switch keyboard layout to Spanish" = "Meta+Q" }',
            'layout_switch_shortcuts = { "Switch keyboard layout to Spanish" = "" }',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_kde_keyboard_setup_values(tmp_path: Path) -> None:
    # The typed values round-trip from the config document.
    config = load_config(write_config(tmp_path, base_config()))
    assert config.kde_keyboard_setup.packages == (
        "libkf6config-bin",
        "qdbus-qt6",
        "python3-dbus",
    )
    assert config.kde_keyboard_setup.username == "i"
    assert config.kde_keyboard_setup.home_dir == "/home/i"
    assert config.kde_keyboard_setup.config_dir == "/home/i/.config"
    assert config.kde_keyboard_setup.kxkbrc_file_name == "kxkbrc"
    assert config.kde_keyboard_setup.layouts == ("us", "ru", "es")
    assert config.kde_keyboard_setup.switch_option == "grp:caps_select"
    assert config.kde_keyboard_setup.reset_old_options is True
    assert config.kde_keyboard_setup.switch_mode == "WinClass"
    assert config.kde_keyboard_setup.use_layout_switching is True
    assert config.kde_keyboard_setup.indicator_display_style == "Flag"
    assert config.kde_keyboard_setup.layout_switch_shortcuts == {
        "Switch keyboard layout to Spanish": "Meta+Q"
    }
    assert config.kde_keyboard_setup.kwin_reload_command == (
        "qdbus6",
        "org.kde.KWin",
        "/KWin",
        "org.kde.KWin.reconfigure",
    )
    assert config.kde_keyboard_setup.panel_restart_command == (
        "systemctl",
        "--user",
        "--machine",
        "i@.host",
        "restart",
        "plasma-plasmashell.service",
    )
