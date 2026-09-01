"""Config tests for the [rustdesk_setup] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import RustdeskOptionConfig, load_config


@pytest.mark.parametrize(
    "content",
    [
        # github_repo is a number, not a string
        base_config().replace(
            'github_repo = "rustdesk/rustdesk"\n',
            "github_repo = 42\n",
        ),
        # download_dir is a number, not a string
        base_config().replace(
            'download_dir = "/var/cache/pyntara/rustdesk"\n',
            "download_dir = 42\n",
        ),
        # id_file_path is a number, not a string
        base_config().replace(
            'id_file_path = "/var/lib/pyntara/rustdesk_id"\n',
            "id_file_path = 42\n",
        ),
        # id_file_mode is not an octal string
        base_config().replace(
            'id_file_mode = "0644"\n', 'id_file_mode = "644"\n'
        ),
        # vault_entry_title is empty
        base_config().replace(
            'vault_entry_title = "rustdesk_password"\n',
            'vault_entry_title = ""\n',
        ),
        # password_words is a string, not an integer
        base_config().replace(
            "password_words = 6\n", 'password_words = "6"\n'
        ),
        # password_separator is empty
        base_config().replace(
            'password_separator = " "\n', 'password_separator = ""\n'
        ),
        # config_dir is a number, not a string
        base_config().replace(
            'config_dir = "/home/i/.config/rustdesk"\n',
            "config_dir = 42\n",
        ),
        # options is not an array
        base_config().replace(
            '[[rustdesk_setup.options]]\n', "[rustdesk_setup.options]\n"
        ),
        # an option entry has an empty key
        base_config().replace(
            'key = "enable-udp-punch"\nvalue = "Y"\n',
            'key = ""\nvalue = "Y"\n',
        ),
        # an option entry has a missing value
        base_config().replace(
            'key = "enable-udp-punch"\nvalue = "Y"\n',
            'key = "enable-udp-punch"\n',
        ),
        # duplicate option keys across entries
        base_config().replace(
            '[[rustdesk_setup.options]]\n'
            'key = "enable-udp-punch"\nvalue = "Y"\n',
            '[[rustdesk_setup.options]]\n'
            'key = "enable-udp-punch"\nvalue = "Y"\n'
            '[[rustdesk_setup.options]]\n'
            'key = "enable-udp-punch"\nvalue = "N"\n',
        ),
        # the vault entry named by vault_entry_title is absent
        base_config().replace(
            '[[vault_structure.entries]]\ntitle = "rustdesk_password"\n'
            'notes = "RustDesk access password."\n',
            "",
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_rustdesk_values(tmp_path: Path) -> None:
    # The typed values round-trip from the config document.
    config = load_config(write_config(tmp_path, base_config()))
    rustdesk = config.rustdesk_setup
    assert rustdesk.github_repo == "rustdesk/rustdesk"
    assert rustdesk.download_dir == Path("/var/cache/pyntara/rustdesk")
    assert rustdesk.id_file_path == Path("/var/lib/pyntara/rustdesk_id")
    assert rustdesk.id_file_mode == 0o644
    assert rustdesk.vault_entry_title == "rustdesk_password"
    assert rustdesk.service_unit_name == "rustdesk.service"
    assert rustdesk.password_words == 6
    assert rustdesk.password_separator == " "
    assert rustdesk.config_dir == Path("/home/i/.config/rustdesk")
    assert rustdesk.install_timeout_seconds == 600
    assert rustdesk.apt_update_timeout_seconds == 600
    assert rustdesk.install_retries == 2
    assert rustdesk.api_timeout_seconds == 30
    assert rustdesk.start_check_attempts == 10
    assert rustdesk.start_check_retry_delay_seconds == 1.0
    assert rustdesk.options == (
        RustdeskOptionConfig(key="enable-udp-punch", value="Y"),
    )


def test_load_config_empty_options(tmp_path: Path) -> None:
    # A missing options array is valid: the task then applies no options.
    content = base_config().replace(
        '[[rustdesk_setup.options]]\nkey = "enable-udp-punch"\nvalue = "Y"\n',
        "",
    )
    config = load_config(write_config(tmp_path, content))
    assert config.rustdesk_setup.options == ()
