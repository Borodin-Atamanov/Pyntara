"""Config tests for the [chrome_setup] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import (
    assert_config_error,
    base_config,
    load_checked_config,
    write_config,
)

CHROME_BLOCK = (
    "[chrome_setup]\n"
    'username = "i"\n'
    'home_dir = "/home/i"\n'
    'settings_repo_url = "https://github.com/Borodin-Atamanov/chromium-default-settings.git"\n'
    'settings_repo_ref = "main"\n'
    'settings_dir = "/var/cache/pyntara/chromium-settings"\n'
    'system_root = "/"\n'
    'apt_source_path = "/etc/apt/sources.list.d/google-chrome.sources"\n'
    'keyring_path = "/usr/share/keyrings/google-chrome.gpg"\n'
    'google_key_url = "https://dl.google.com/linux/linux_signing_key.pub"\n'
    'desktop_source_path = "/usr/share/applications/google-chrome.desktop"\n'
    'desktop_override_path = "/usr/local/share/applications/google-chrome.desktop"\n'
    "cdp_port = 19222\n"
    'cdp_address = "127.0.0.1"\n'
)


@pytest.mark.parametrize(
    "content",
    [
        # username is a number, not a string
        base_config().replace('username = "i"\n', "username = 7\n"),
        # username is empty
        base_config().replace('username = "i"\n', 'username = ""\n'),
        # settings_repo_url is a number, not a string
        base_config().replace(
            'settings_repo_url = "https://github.com/Borodin-Atamanov/chromium-default-settings.git"\n',
            "settings_repo_url = 7\n",
        ),
        # settings_repo_ref is empty
        base_config().replace('settings_repo_ref = "main"\n', 'settings_repo_ref = ""\n'),
        # settings_dir is a number, not a string
        base_config().replace(
            'settings_dir = "/var/cache/pyntara/chromium-settings"\n',
            "settings_dir = 7\n",
        ),
        # system_root is a number, not a string
        base_config().replace('system_root = "/"\n', "system_root = 7\n"),
        # apt_source_path is empty
        base_config().replace(
            'apt_source_path = "/etc/apt/sources.list.d/google-chrome.sources"\n',
            'apt_source_path = ""\n',
        ),
        # keyring_path is a number, not a string
        base_config().replace(
            'keyring_path = "/usr/share/keyrings/google-chrome.gpg"\n',
            "keyring_path = 7\n",
        ),
        # google_key_url is empty
        base_config().replace(
            'google_key_url = "https://dl.google.com/linux/linux_signing_key.pub"\n',
            'google_key_url = ""\n',
        ),
        # desktop_source_path is a number, not a string
        base_config().replace(
            'desktop_source_path = "/usr/share/applications/google-chrome.desktop"\n',
            "desktop_source_path = 7\n",
        ),
        # desktop_override_path is empty
        base_config().replace(
            'desktop_override_path = "/usr/local/share/applications/google-chrome.desktop"\n',
            'desktop_override_path = ""\n',
        ),
        # cdp_port is zero
        base_config().replace("cdp_port = 19222\n", "cdp_port = 0\n"),
        # cdp_port is negative
        base_config().replace("cdp_port = 19222\n", "cdp_port = -1\n"),
        # cdp_port is a string, not an integer
        base_config().replace("cdp_port = 19222\n", 'cdp_port = "19222"\n'),
        # cdp_address is a number, not a string
        base_config().replace('cdp_address = "127.0.0.1"\n', "cdp_address = 7\n"),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_missing_chrome_section_raises(tmp_path: Path) -> None:
    # The section is mandatory: without it the task has no target user,
    # repository or CDP port (architecture contract, Configuration).
    assert_config_error(
        tmp_path,
        base_config().replace(CHROME_BLOCK, ""),
        match="\\[chrome_setup\\]",
    )


def test_load_config_typed_values(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, base_config())
    config = load_checked_config(config_path)
    assert config.chrome_setup.username == "i"
    assert config.chrome_setup.home_dir == "/home/i"
    assert config.chrome_setup.settings_repo_url == (
        "https://github.com/Borodin-Atamanov/chromium-default-settings.git"
    )
    assert config.chrome_setup.settings_repo_ref == "main"
    assert config.chrome_setup.settings_dir == Path(
        "/var/cache/pyntara/chromium-settings"
    )
    assert config.chrome_setup.system_root == Path("/")
    assert config.chrome_setup.apt_source_path == Path(
        "/etc/apt/sources.list.d/google-chrome.sources"
    )
    assert config.chrome_setup.keyring_path == Path(
        "/usr/share/keyrings/google-chrome.gpg"
    )
    assert config.chrome_setup.google_key_url == (
        "https://dl.google.com/linux/linux_signing_key.pub"
    )
    assert config.chrome_setup.desktop_source_path == Path(
        "/usr/share/applications/google-chrome.desktop"
    )
    assert config.chrome_setup.desktop_override_path == Path(
        "/usr/local/share/applications/google-chrome.desktop"
    )
    assert config.chrome_setup.cdp_port == 19222
    assert config.chrome_setup.cdp_address == "127.0.0.1"
