"""Config tests for the [imagemagick_setup] table."""

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
            'packages = ["imagemagick"]', 'packages = "imagemagick"'
        ),
        # packages contains a number, not strings
        base_config().replace('packages = ["imagemagick"]', "packages = [1]"),
        # package_status_timeout_seconds of this section is a string
        base_config().replace(
            'packages = ["imagemagick"]\npackage_status_timeout_seconds = 30\n',
            'packages = ["imagemagick"]\npackage_status_timeout_seconds = "30"\n',
        ),
        # package_install_retries is a string
        base_config().replace(
            'packages = ["imagemagick"]\npackage_status_timeout_seconds = 30\n'
            "package_install_retries = 3\n",
            'packages = ["imagemagick"]\npackage_status_timeout_seconds = 30\n'
            'package_install_retries = "3"\n',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_imagemagick_values(tmp_path: Path) -> None:
    # The typed values round-trip from the config document.
    config = load_config(write_config(tmp_path, base_config()))
    assert config.imagemagick_setup.packages == ("imagemagick",)
    assert config.imagemagick_setup.package_status_timeout_seconds == 30
    assert config.imagemagick_setup.package_install_retries == 3
