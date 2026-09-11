"""Config tests for the [vocalinux_setup] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_checks import ConfigError
from config_helpers import (
    assert_config_error,
    base_config,
    load_checked_config,
    write_config,
)

SECTION = (
    "[vocalinux_setup]\n"
    'username = "i"\n'
    'home_dir = "/home/i"\n'
    'download_dir = "/var/cache/pyntara/vocalinux"\n'
    'version = "0.16.2"\n'
    'github_repo = "VocaHQ/vocalinux"\n'
    'asset_name_template = "Vocalinux-{version}-{asset_arch}.AppImage"\n'
    'packages = ["wtype", "ydotool", "wl-clipboard", "libkf6config-bin"]\n'
    'input_group = "input"\n'
    'appimage_dir_relative_path = ".local/share/vocalinux/appimage"\n'
    'app_config_relative_path = ".config/vocalinux/config.json"\n'
    'autostart_relative_path = ".config/autostart/vocalinux.desktop"\n'
    'echo_desktop_relative_path = ".local/share/applications/net.local.echo.desktop"\n'
    'app_config_template_file_name = "config.json"\n'
    'autostart_template_file_name = "vocalinux.desktop"\n'
    'echo_desktop_template_file_name = "net.local.echo.desktop"\n'
    'shortcuts_file_name = "kglobalshortcutsrc"\n'
    'shortcut_group_name = "services"\n'
    'shortcut_entry_name = "net.local.echo.desktop"\n'
    'shortcut_action_name = "_launch"\n'
    'shortcut_key_sequence = "Meta+S"\n'
    'service_unit_name = "ydotool.service"\n'
    "package_status_timeout_seconds = 30\n"
    "package_install_retries = 3\n"
    'user_file_mode = "0644"\n'
    'executable_file_mode = "0755"\n'
)


def _section_variant(content: str) -> str:
    """The base config with the [vocalinux_setup] section text replaced.

    The full section text is unique in the document, so mutating a value
    inside it targets only the vocalinux section and never a sibling table
    that carries the same field names.
    """

    return base_config().replace(SECTION, content)


def test_valid_section_loads(tmp_path: Path) -> None:
    """A valid [vocalinux_setup] section loads into the config."""

    config = load_checked_config(write_config(tmp_path, base_config()))
    setup = config.vocalinux_setup
    assert setup.username == "i"
    assert setup.home_dir == "/home/i"
    assert setup.download_dir == Path("/var/cache/pyntara/vocalinux")
    assert setup.version == "0.16.2"
    assert setup.packages == ("wtype", "ydotool", "wl-clipboard", "libkf6config-bin")
    assert setup.input_group == "input"
    assert setup.service_unit_name == "ydotool.service"
    assert setup.user_file_mode == 0o644
    assert setup.executable_file_mode == 0o755
    assert setup.package_status_timeout_seconds == 30
    assert setup.package_install_retries == 3


@pytest.mark.parametrize(
    "content",
    [
        # username is a number, not a string
        SECTION.replace('username = "i"\n', "username = 42\n"),
        # username is an empty string
        SECTION.replace('username = "i"\n', 'username = ""\n'),
        # home_dir is a number, not a string
        SECTION.replace('home_dir = "/home/i"\n', "home_dir = 42\n"),
        # download_dir is an empty string
        SECTION.replace(
            'download_dir = "/var/cache/pyntara/vocalinux"\n',
            'download_dir = ""\n',
        ),
        # version is an empty string
        SECTION.replace('version = "0.16.2"\n', 'version = ""\n'),
        # packages is a string, not an array
        SECTION.replace(
            'packages = ["wtype", "ydotool", "wl-clipboard", "libkf6config-bin"]\n',
            'packages = "wtype"\n',
        ),
        # packages is an empty array
        SECTION.replace(
            'packages = ["wtype", "ydotool", "wl-clipboard", "libkf6config-bin"]\n',
            "packages = []\n",
        ),
        # packages contains a number, not a string
        SECTION.replace(
            'packages = ["wtype", "ydotool", "wl-clipboard", "libkf6config-bin"]\n',
            'packages = ["wtype", 1]\n',
        ),
        # input_group is an empty string
        SECTION.replace('input_group = "input"\n', 'input_group = ""\n'),
        # service_unit_name is an empty string
        SECTION.replace(
            'service_unit_name = "ydotool.service"\n',
            'service_unit_name = ""\n',
        ),
        # package_status_timeout_seconds is a string
        SECTION.replace(
            "package_status_timeout_seconds = 30\n",
            'package_status_timeout_seconds = "30"\n',
        ),
        # package_install_retries is a boolean
        SECTION.replace(
            "package_install_retries = 3\n",
            "package_install_retries = true\n",
        ),
        # user_file_mode is a number, not the readable octal string
        SECTION.replace('user_file_mode = "0644"\n', "user_file_mode = 420\n"),
        # executable_file_mode has not the four digits a mode is written with
        SECTION.replace(
            'executable_file_mode = "0755"\n', 'executable_file_mode = "755"\n'
        ),
        # asset_name_template is a number, not a string
        SECTION.replace(
            'asset_name_template = "Vocalinux-{version}-{asset_arch}.AppImage"\n',
            "asset_name_template = 42\n",
        ),
        # asset_name_template is an empty string
        SECTION.replace(
            'asset_name_template = "Vocalinux-{version}-{asset_arch}.AppImage"\n',
            'asset_name_template = ""\n',
        ),
        # appimage_dir_relative_path is an empty string
        SECTION.replace(
            'appimage_dir_relative_path = ".local/share/vocalinux/appimage"\n',
            'appimage_dir_relative_path = ""\n',
        ),
        # autostart_relative_path is a number, not a string
        SECTION.replace(
            'autostart_relative_path = ".config/autostart/vocalinux.desktop"\n',
            "autostart_relative_path = 42\n",
        ),
        # echo_desktop_template_file_name is an empty string
        SECTION.replace(
            'echo_desktop_template_file_name = "net.local.echo.desktop"\n',
            'echo_desktop_template_file_name = ""\n',
        ),
        # shortcut_key_sequence is a number, not a string
        SECTION.replace(
            'shortcut_key_sequence = "Meta+S"\n',
            "shortcut_key_sequence = 42\n",
        ),
        # shortcuts_file_name is an empty string
        SECTION.replace(
            'shortcuts_file_name = "kglobalshortcutsrc"\n',
            'shortcuts_file_name = ""\n',
        ),
    ],
)
def test_wrong_value_raises(tmp_path: Path, content: str) -> None:
    """One mutated value makes the section invalid."""

    assert_config_error(tmp_path, _section_variant(content))


def test_missing_section_raises(tmp_path: Path) -> None:
    """A document without the section is a ConfigError."""

    with pytest.raises(ConfigError):
        load_checked_config(write_config(tmp_path, base_config().replace(SECTION, "")))
