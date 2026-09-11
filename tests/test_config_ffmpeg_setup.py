"""Config tests for the [ffmpeg_setup] table."""

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
        # packages is a string, not an array
        base_config().replace(
            'packages = ["ffmpeg"]', 'packages = "ffmpeg"'
        ),
        # packages contains a number, not strings
        base_config().replace('packages = ["ffmpeg"]', "packages = [1]"),
        # wayrecord_bin_path is a number, not a string
        base_config().replace(
            'wayrecord_bin_path = "/usr/local/bin/pyntara-wayrecord"',
            "wayrecord_bin_path = 42",
        ),
        # wayrecord_desktop_path is a number, not a string
        base_config().replace(
            'wayrecord_desktop_path = "/usr/share/applications/pyntara-wayrecord.desktop"',
            "wayrecord_desktop_path = 42",
        ),
        # wayrecord_file_mode is a number, not the readable octal string
        base_config().replace(
            'wayrecord_file_mode = "0755"\n', "wayrecord_file_mode = 493\n"
        ),
        # wayrecord_file_mode has not the four digits a mode is written with
        base_config().replace(
            'wayrecord_file_mode = "0755"\n', 'wayrecord_file_mode = "755"\n'
        ),
        # package_status_timeout_seconds of this section is a string
        base_config().replace(
            "package_status_timeout_seconds = 30\n",
            'package_status_timeout_seconds = "30"\n',
        ),
        # package_install_retries is a string
        base_config().replace(
            "package_status_timeout_seconds = 30\n"
            "package_install_retries = 3\n",
            "package_status_timeout_seconds = 30\n"
            'package_install_retries = "3"\n',
        ),
        # wayrecord_source_file_names is a string, not an array
        base_config().replace(
            'wayrecord_source_file_names = ["wayrecord.c", "zkde-screencast-client.c"]',
            'wayrecord_source_file_names = "wayrecord.c"',
        ),
        # wayrecord_desktop_template_file_name is a number, not a string
        base_config().replace(
            'wayrecord_desktop_template_file_name = "pyntara-wayrecord.desktop"',
            "wayrecord_desktop_template_file_name = 42",
        ),
        # wayrecord_build_file_suffix is an empty string
        base_config().replace(
            'wayrecord_build_file_suffix = ".build"',
            'wayrecord_build_file_suffix = ""',
        ),
        # wayrecord_build_flags_command is an empty array
        base_config().replace(
            'wayrecord_build_flags_command = ["pkg-config", "--cflags", "--libs", "wayland-client", "libpipewire-0.3"]',
            "wayrecord_build_flags_command = []",
        ),
        # wayrecord_compile_command is an empty array
        base_config().replace(
            'wayrecord_compile_command = ["gcc", "-O2", "-o", "{output}"]',
            "wayrecord_compile_command = []",
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_ffmpeg_values(tmp_path: Path) -> None:
    # The typed values round-trip from the config document.
    config = load_checked_config(write_config(tmp_path, base_config()))
    assert config.ffmpeg_setup.packages == ("ffmpeg",)
    assert config.ffmpeg_setup.wayrecord_bin_path == Path(
        "/usr/local/bin/pyntara-wayrecord"
    )
    assert config.ffmpeg_setup.wayrecord_desktop_path == Path(
        "/usr/share/applications/pyntara-wayrecord.desktop"
    )
    assert config.ffmpeg_setup.wayrecord_file_mode == 0o755
    assert config.ffmpeg_setup.wayrecord_source_file_names == (
        "wayrecord.c",
        "zkde-screencast-client.c",
    )
    assert config.ffmpeg_setup.wayrecord_desktop_template_file_name == (
        "pyntara-wayrecord.desktop"
    )
    assert config.ffmpeg_setup.wayrecord_build_file_suffix == ".build"
    assert config.ffmpeg_setup.wayrecord_build_flags_command == (
        "pkg-config",
        "--cflags",
        "--libs",
        "wayland-client",
        "libpipewire-0.3",
    )
    assert config.ffmpeg_setup.wayrecord_compile_command == (
        "gcc",
        "-O2",
        "-o",
        "{output}",
    )
    assert config.ffmpeg_setup.package_status_timeout_seconds == 30
    assert config.ffmpeg_setup.package_install_retries == 3
