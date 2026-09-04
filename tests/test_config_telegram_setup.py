"""Config tests for the [telegram_setup] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


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
        'icon_url = "https://example.invalid/telegram/icon512.png"\n'
    )
    assert_config_error(
        tmp_path,
        base_config().replace(telegram_block, ""),
        match="\\[telegram_setup\\]",
    )


def test_load_config_typed_values(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, base_config())
    config = load_config(config_path)
    assert config.telegram_setup.username == "i"
    assert config.telegram_setup.home_dir == "/home/i"
    assert config.telegram_setup.download_dir == Path("/var/cache/pyntara/telegram")
    assert config.telegram_setup.latest_url == "https://telegram.org/dl/desktop/linux"
    assert config.telegram_setup.icon_url == (
        "https://example.invalid/telegram/icon512.png"
    )
