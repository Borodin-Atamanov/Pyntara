"""Config tests for the [scrcpy_setup] table."""

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
        # github_repo is empty
        base_config().replace(
            'github_repo = "Genymobile/scrcpy"', 'github_repo = ""'
        ),
        # archive_name_template lost the asset architecture placeholder
        base_config().replace(
            'archive_name_template = "scrcpy-linux-{asset_arch}-{release_tag}.tar.gz"',
            'archive_name_template = "scrcpy-linux-{release_tag}.tar.gz"',
        ),
        # archive_name_template lost the release tag placeholder
        base_config().replace(
            'archive_name_template = "scrcpy-linux-{asset_arch}-{release_tag}.tar.gz"',
            'archive_name_template = "scrcpy-linux-{asset_arch}.tar.gz"',
        ),
        # checksum_file_name is empty
        base_config().replace(
            'checksum_file_name = "SHA256SUMS.txt"', 'checksum_file_name = ""'
        ),
        # fallback_packages is a string, not an array
        base_config().replace(
            'fallback_packages = ["scrcpy"]', 'fallback_packages = "scrcpy"'
        ),
        # udev_rules_package_name is a number, not a string
        base_config().replace(
            'udev_rules_package_name = "android-udev-rules"',
            "udev_rules_package_name = 7",
        ),
        # apt_binary_path is empty
        base_config().replace(
            'apt_binary_path = "/usr/bin/scrcpy"', 'apt_binary_path = ""'
        ),
        # theme_icon_name is empty
        base_config().replace(
            'theme_icon_name = "scrcpy"', 'theme_icon_name = ""'
        ),
        # download_dir is empty
        base_config().replace(
            'download_dir = "/var/cache/pyntara/scrcpy"', 'download_dir = ""'
        ),
        # install_dir_relative_path is empty
        base_config().replace(
            'install_dir_relative_path = ".local/share/scrcpy"',
            'install_dir_relative_path = ""',
        ),
        # command_relative_path is a number, not a string
        base_config().replace(
            'command_relative_path = ".local/bin/scrcpy"',
            "command_relative_path = 7",
        ),
        # launcher_relative_path is empty
        base_config().replace(
            'launcher_relative_path = ".local/share/applications/scrcpy.desktop"',
            'launcher_relative_path = ""',
        ),
        # console_launcher_relative_path is empty
        base_config().replace(
            'console_launcher_relative_path = ".local/share/applications/scrcpy-console.desktop"',
            'console_launcher_relative_path = ""',
        ),
        # binary_file_name is empty
        base_config().replace('binary_file_name = "scrcpy"', 'binary_file_name = ""'),
        # server_file_name is a number, not a string
        base_config().replace(
            'server_file_name = "scrcpy-server"', "server_file_name = 7"
        ),
        # adb_file_name is empty
        base_config().replace('adb_file_name = "adb"', 'adb_file_name = ""'),
        # icon_file_name is empty
        base_config().replace('icon_file_name = "scrcpy.png"', 'icon_file_name = ""'),
        # extract_dir_prefix is empty
        base_config().replace(
            'extract_dir_prefix = "pyntara-scrcpy-"', 'extract_dir_prefix = ""'
        ),
        # trash_dir_relative_path is empty
        base_config().replace(
            'trash_dir_relative_path = ".local/share/Trash/files"',
            'trash_dir_relative_path = ""',
        ),
        # version_command lost the client path placeholder
        base_config().replace(
            'version_command = ["{binary}", "--version"]',
            'version_command = ["scrcpy", "--version"]',
        ),
        # checksum_command is a string, not an array
        base_config().replace(
            'checksum_command = ["sha256sum", "{file}"]',
            'checksum_command = "sha256sum"',
        ),
        # checksum_command lost the file placeholder
        base_config().replace(
            'checksum_command = ["sha256sum", "{file}"]',
            'checksum_command = ["sha256sum"]',
        ),
        # archive_extract_command lost the extract directory placeholder
        base_config().replace(
            'archive_extract_command = ["tar", "--extract", "--gzip", "--file", '
            '"{archive}", "--directory", "{extract_dir}"]',
            'archive_extract_command = ["tar", "--extract", "--gzip", "--file", '
            '"{archive}"]',
        ),
        # launcher_file_mode has not the four digits a mode is written with
        base_config().replace(
            'launcher_file_mode = "0644"\n', 'launcher_file_mode = "644"\n'
        ),
        # executable_file_mode is a number, not the readable octal string
        base_config().replace(
            'executable_file_mode = "0755"\n', "executable_file_mode = 493\n"
        ),
        # package_status_timeout_seconds is not a positive integer
        base_config().replace(
            "package_status_timeout_seconds = 30",
            "package_status_timeout_seconds = 0",
        ),
        # package_install_retries is a string, not an integer
        base_config().replace(
            "package_install_retries = 3", 'package_install_retries = "3"'
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_missing_scrcpy_section_raises(tmp_path: Path) -> None:
    # The section is mandatory: without it the task has no repository, no
    # install directory and no fallback packages (architecture contract,
    # Configuration).
    scrcpy_block = (
        "[scrcpy_setup]\n"
        'username = "i"\n'
        'home_dir = "/home/i"\n'
        'github_repo = "Genymobile/scrcpy"\n'
        'archive_name_template = "scrcpy-linux-{asset_arch}-{release_tag}.tar.gz"\n'
        'checksum_file_name = "SHA256SUMS.txt"\n'
        'fallback_packages = ["scrcpy"]\n'
        'udev_rules_package_name = "android-udev-rules"\n'
        'apt_binary_path = "/usr/bin/scrcpy"\n'
        'theme_icon_name = "scrcpy"\n'
        'download_dir = "/var/cache/pyntara/scrcpy"\n'
        'install_dir_relative_path = ".local/share/scrcpy"\n'
        'command_relative_path = ".local/bin/scrcpy"\n'
        'launcher_relative_path = ".local/share/applications/scrcpy.desktop"\n'
        'console_launcher_relative_path = ".local/share/applications/scrcpy-console.desktop"\n'
        'launcher_template_file_name = "scrcpy.desktop"\n'
        'console_launcher_template_file_name = "scrcpy-console.desktop"\n'
        'binary_file_name = "scrcpy"\n'
        'server_file_name = "scrcpy-server"\n'
        'adb_file_name = "adb"\n'
        'icon_file_name = "scrcpy.png"\n'
        'extract_dir_prefix = "pyntara-scrcpy-"\n'
        'trash_dir_relative_path = ".local/share/Trash/files"\n'
        'version_command = ["{binary}", "--version"]\n'
        'checksum_command = ["sha256sum", "{file}"]\n'
        'archive_extract_command = ["tar", "--extract", "--gzip", "--file", '
        '"{archive}", "--directory", "{extract_dir}"]\n'
        'launcher_file_mode = "0644"\n'
        'executable_file_mode = "0755"\n'
        "package_status_timeout_seconds = 30\n"
        "package_install_retries = 3\n"
    )
    assert_config_error(
        tmp_path,
        base_config().replace(scrcpy_block, ""),
        match="\\[scrcpy_setup\\]",
    )


def test_load_config_typed_values(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, base_config())
    config = load_checked_config(config_path)
    assert config.scrcpy_setup.username == "i"
    assert config.scrcpy_setup.home_dir == "/home/i"
    assert config.scrcpy_setup.github_repo == "Genymobile/scrcpy"
    assert config.scrcpy_setup.download_dir == Path("/var/cache/pyntara/scrcpy")
    assert config.scrcpy_setup.apt_binary_path == Path("/usr/bin/scrcpy")
    assert config.scrcpy_setup.fallback_packages == ("scrcpy",)
    assert config.scrcpy_setup.launcher_file_mode == 0o644
    assert config.scrcpy_setup.executable_file_mode == 0o755
    assert config.scrcpy_setup.package_install_retries == 3
