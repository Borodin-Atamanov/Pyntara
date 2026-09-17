"""[scrcpy_setup] table: the scrcpy Android screen mirroring client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScrcpySetupConfig:
    """scrcpy installed for the desktop user.

    username and home_dir identify the user who runs the client; the
    install directory, the version directories inside it and the command in
    the user prefix are derived under that home from
    install_dir_relative_path and command_relative_path, and
    binary_file_name, server_file_name, adb_file_name and icon_file_name
    name the files the release archive carries. github_repo and
    archive_name_template locate the release archive of the newest release,
    whose {asset_arch} part comes from the engine
    release_asset_architectures mapping and whose {release_tag} part is the
    release tag as it is; checksum_file_name is the checksum file of the
    same release and checksum_command prints the digest of a downloaded
    file, which the task compares with the line naming the archive.
    fallback_packages is the Ubuntu archive client installed when the
    release path is unavailable, apt_binary_path is where that install puts
    its binary, and udev_rules_package_name carries the Android USB rules
    the release archive does not ship. download_dir is the root cache of
    the archive, extract_dir_prefix names the temporary directory it is
    unpacked into, archive_extract_command is the unpacking call, and
    version_command probes the delivered client. launcher_relative_path,
    console_launcher_relative_path, launcher_template_file_name and
    console_launcher_template_file_name are the two menu entries and their
    templates under task_data/scrcpy_setup/ of the clone, rendered with the
    client and the icon paths; theme_icon_name is the icon the desktop
    resolves through its theme when the release tree is not installed.
    trash_dir_relative_path is the user trash a superseded version
    directory is moved into, and launcher_file_mode with
    executable_file_mode are the modes of the deployed files; the package
    status timeout and the install retries bound the fallback path
    (docs/spec/scrcpy-setup.md).
    """

    username: str
    home_dir: str
    github_repo: str
    archive_name_template: str
    checksum_file_name: str
    fallback_packages: tuple[str, ...]
    udev_rules_package_name: str
    apt_binary_path: Path
    theme_icon_name: str
    download_dir: Path
    install_dir_relative_path: str
    command_relative_path: str
    launcher_relative_path: str
    console_launcher_relative_path: str
    launcher_template_file_name: str
    console_launcher_template_file_name: str
    binary_file_name: str
    server_file_name: str
    adb_file_name: str
    icon_file_name: str
    extract_dir_prefix: str
    trash_dir_relative_path: str
    version_command: tuple[str, ...]
    checksum_command: tuple[str, ...]
    archive_extract_command: tuple[str, ...]
    launcher_file_mode: int
    executable_file_mode: int
    package_status_timeout_seconds: int
    package_install_retries: int
