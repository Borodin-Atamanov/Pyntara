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


def _kde_settings_with_kconfig(records: str) -> str:
    """base_config() with [[kde_settings.kconfig]] records appended.

    The records are inserted after the kde_settings kwin_reload_command,
    the last key of the [kde_settings] table in base_config(): in TOML a
    [[kde_settings.kconfig]] header would otherwise capture the following
    plain keys into the last record element.
    """

    reload_line = (
        'kwin_reload_command = ["qdbus6", "org.kde.KWin", "/KWin", '
        '"org.kde.KWin.reconfigure"]\n'
    )
    tail = "[swapfile_service_install]"
    return base_config().replace(
        reload_line + tail, reload_line + records + "\n" + tail
    )


def test_load_config_kde_settings_kconfig_values(tmp_path: Path) -> None:
    # The kconfig records round-trip from the config document.
    records = """\
[[kde_settings.kconfig]]
file = "kwinrc"
group = ["TabBox"]
key = "LayoutName"
value = "coverswitch"

[[kde_settings.kconfig]]
file = "kdeglobals"
group = ["KDE"]
key = "SingleClick"
value = "true"
type = "bool"

[[kde_settings.kconfig]]
file = "kwinrc"
group = ["Effect-login"]
key = "FadeToBlack"
value = "true"
type = "bool"

[[kde_settings.kconfig]]
file = "kwinrc"
group = ["TabBox"]
key = "StaleKey"
delete = true
"""
    config = load_config(write_config(tmp_path, _kde_settings_with_kconfig(records)))
    assert config.kde_settings.kconfig[0].file == "kwinrc"
    assert config.kde_settings.kconfig[0].group == ("TabBox",)
    assert config.kde_settings.kconfig[0].key == "LayoutName"
    assert config.kde_settings.kconfig[0].value == "coverswitch"
    assert config.kde_settings.kconfig[0].type == "string"
    assert config.kde_settings.kconfig[0].delete is False
    assert config.kde_settings.kconfig[1].group == ("KDE",)
    assert config.kde_settings.kconfig[1].type == "bool"
    assert config.kde_settings.kconfig[2].type == "bool"
    assert config.kde_settings.kconfig[3].delete is True
    assert config.kde_settings.kconfig[3].value == ""


def test_load_config_kde_settings_no_kconfig_is_empty(tmp_path: Path) -> None:
    # Without the kconfig list the field defaults to an empty tuple.
    config = load_config(write_config(tmp_path, base_config()))
    assert config.kde_settings.kconfig == ()


@pytest.mark.parametrize(
    "records",
    [
        # kconfig is a table, not an array of tables
        "kconfig = {}",
        # kconfig is a string, not an array
        'kconfig = "kwinrc"',
        # a record is missing file
        """\
[[kde_settings.kconfig]]
group = ["General"]
key = "Foo"
value = "1"
""",
        # a record has an empty group
        """\
[[kde_settings.kconfig]]
file = "kwinrc"
group = []
key = "Foo"
value = "1"
""",
        # a record is missing key
        """\
[[kde_settings.kconfig]]
file = "kwinrc"
group = ["General"]
value = "1"
""",
        # a record is missing value without delete
        """\
[[kde_settings.kconfig]]
file = "kwinrc"
group = ["General"]
key = "Foo"
""",
        # a record has both value and delete
        """\
[[kde_settings.kconfig]]
file = "kwinrc"
group = ["General"]
key = "Foo"
value = "1"
delete = true
""",
        # a record has a type outside the vocabulary
        """\
[[kde_settings.kconfig]]
file = "kwinrc"
group = ["General"]
key = "Foo"
value = "1"
type = "int"
""",
        # a record has delete as a string, not a boolean
        """\
[[kde_settings.kconfig]]
file = "kwinrc"
group = ["General"]
key = "Foo"
value = "1"
delete = "true"
""",
    ],
)
def test_load_config_kde_settings_kconfig_wrong_types_raise(
    tmp_path: Path, records: str
) -> None:
    assert_config_error(tmp_path, _kde_settings_with_kconfig(records))
