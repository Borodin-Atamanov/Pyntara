"""Config tests for the [telegram_setup] table."""

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
        # username is a number, not a string
        base_config().replace('username = "i"\n', "username = 7\n"),
        # username is empty
        base_config().replace('username = "i"\n', 'username = ""\n'),
        # home_dir is a number, not a string
        base_config().replace('home_dir = "/home/i"\n', "home_dir = 7\n"),
        # home_dir is empty
        base_config().replace('home_dir = "/home/i"\n', 'home_dir = ""\n'),
        # download_dir is a number, not a string
        base_config().replace(
            'download_dir = "/var/cache/pyntara/telegram"', "download_dir = 7"
        ),
        # download_dir is empty
        base_config().replace(
            'download_dir = "/var/cache/pyntara/telegram"',
            'download_dir = ""',
        ),
        # latest_url is a number, not a string
        base_config().replace(
            'latest_url = "https://telegram.org/dl/desktop/linux"',
            "latest_url = 7",
        ),
        # latest_url is empty
        base_config().replace(
            'latest_url = "https://telegram.org/dl/desktop/linux"',
            'latest_url = ""',
        ),
        # icon_url is a number, not a string
        base_config().replace(
            'icon_url = "https://example.invalid/telegram/icon512.png"',
            "icon_url = 7",
        ),
        # icon_url is empty
        base_config().replace(
            'icon_url = "https://example.invalid/telegram/icon512.png"',
            'icon_url = ""',
        ),
        # executable_file_mode is a number, not the readable octal string
        base_config().replace(
            'executable_file_mode = "0755"\n', "executable_file_mode = 493\n"
        ),
        # launcher_file_mode has not the four digits a mode is written with
        base_config().replace(
            'launcher_file_mode = "0644"\n', 'launcher_file_mode = "644"\n'
        ),
        # install_dir_relative_path is a number, not a string
        base_config().replace(
            'install_dir_relative_path = ".local/share/Telegram"',
            "install_dir_relative_path = 7",
        ),
        # install_dir_relative_path is empty
        base_config().replace(
            'install_dir_relative_path = ".local/share/Telegram"',
            'install_dir_relative_path = ""',
        ),
        # launcher_relative_path is empty
        base_config().replace(
            'launcher_relative_path = ".local/share/applications/telegramdesktop.desktop"',
            'launcher_relative_path = ""',
        ),
        # icon_relative_path is empty
        base_config().replace(
            'icon_relative_path = ".local/share/icons/telegram-desktop.png"',
            'icon_relative_path = ""',
        ),
        # binary_file_name is empty
        base_config().replace(
            'binary_file_name = "Telegram"', 'binary_file_name = ""'
        ),
        # updater_file_name is a number, not a string
        base_config().replace(
            'updater_file_name = "Updater"', "updater_file_name = 7"
        ),
        # archive_directory_name is empty
        base_config().replace(
            'archive_directory_name = "Telegram"',
            'archive_directory_name = ""',
        ),
        # extract_dir_prefix is empty
        base_config().replace(
            'extract_dir_prefix = "pyntara-telegram-"',
            'extract_dir_prefix = ""',
        ),
        # launcher_template_file_name is empty
        base_config().replace(
            'launcher_template_file_name = "telegramdesktop.desktop"',
            'launcher_template_file_name = ""',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_missing_telegram_section_raises(tmp_path: Path) -> None:
    # The section is mandatory: without it the task has no target user or
    # cache (architecture contract, Configuration).
    telegram_block = (
        "[telegram_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'download_dir = "/var/cache/pyntara/telegram"\n'
        'latest_url = "https://telegram.org/dl/desktop/linux"\n'
        'latest_url_command = ["curl", "--fail", "--head", "--write-out", "%{url_effective}"]\n'
        'icon_url = "https://example.invalid/telegram/icon512.png"\n'
        'install_dir_relative_path = ".local/share/Telegram"\n'
        'launcher_relative_path = ".local/share/applications/telegramdesktop.desktop"\n'
        'icon_relative_path = ".local/share/icons/telegram-desktop.png"\n'
        'binary_file_name = "Telegram"\n'
        'updater_file_name = "Updater"\n'
        'archive_directory_name = "Telegram"\n'
        'extract_dir_prefix = "pyntara-telegram-"\n'
        'launcher_template_file_name = "telegramdesktop.desktop"\n'
        'tar_extract_command = ["tar", "--extract", "--file", "{archive}", '
        '"--directory", "{extract_dir}"]\n'
        'launcher_file_mode = "0644"\n'
        'icon_file_mode = "0644"\n'
        'executable_file_mode = "0755"\n'
    )
    assert_config_error(
        tmp_path,
        base_config().replace(telegram_block, ""),
        match="\\[telegram_setup\\]",
    )


def test_load_config_typed_values(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, base_config())
    config = load_checked_config(config_path)
    assert config.telegram_setup.username == "i"
    assert config.telegram_setup.home_dir == "/home/i"
    assert config.telegram_setup.download_dir == Path("/var/cache/pyntara/telegram")
    assert config.telegram_setup.latest_url == "https://telegram.org/dl/desktop/linux"
    assert config.telegram_setup.icon_url == (
        "https://example.invalid/telegram/icon512.png"
    )
    assert config.telegram_setup.launcher_file_mode == 0o644
    assert config.telegram_setup.icon_file_mode == 0o644
    assert config.telegram_setup.executable_file_mode == 0o755
