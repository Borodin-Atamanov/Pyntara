"""Config tests for the [hostname] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


@pytest.mark.parametrize(
    "content",
    [
        # hostname_file is a number, not a string
        base_config().replace('hostname_file = "/etc/hostname"', "hostname_file = 42"),
        # hostname_file is an empty string
        base_config().replace('hostname_file = "/etc/hostname"', 'hostname_file = ""'),
        # set_hostname_command is a string, not an array
        base_config().replace(
            'set_hostname_command = ["hostnamectl", "set-hostname"]',
            'set_hostname_command = "hostnamectl"',
        ),
        # set_hostname_command is an empty array
        base_config().replace(
            'set_hostname_command = ["hostnamectl", "set-hostname"]',
            "set_hostname_command = []",
        ),
        # set_hostname_command contains a number, not strings
        base_config().replace(
            'set_hostname_command = ["hostnamectl", "set-hostname"]',
            "set_hostname_command = [1]",
        ),
        # set_hostname_command contains an empty string
        base_config().replace(
            'set_hostname_command = ["hostnamectl", "set-hostname"]',
            'set_hostname_command = [""]',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_hostname_values(tmp_path: Path) -> None:
    # The typed values round-trip from the config document.
    config = load_config(write_config(tmp_path, base_config()))
    assert config.hostname.hostname_file == "/etc/hostname"
    assert config.hostname.set_hostname_command == (
        "hostnamectl",
        "set-hostname",
    )
