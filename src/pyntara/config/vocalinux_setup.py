"""[vocalinux_setup] table: the Vocalinux voice dictation app."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VocalinuxSetupConfig:
    """Vocalinux dictation installed for the desktop user.

    username and home_dir identify the desktop user who runs Vocalinux;
    the AppImage, the app config, the autostart entry and the empty action
    desktop file are derived under that home from
    appimage_dir_relative_path, app_config_relative_path,
    autostart_relative_path and echo_desktop_relative_path, and their
    bodies come from the templates app_config_template_file_name,
    autostart_template_file_name and echo_desktop_template_file_name under
    task_data/vocalinux_setup/ of the clone (the autostart template is
    rendered with $appimage). download_dir is the
    root cache that keeps the AppImage of the pinned version. version is
    the pinned Vocalinux release the task installs, without a leading v;
    the release tag and the asset name are derived from it, and
    asset_name_template is the name of that asset with {version} and
    {asset_arch} substituted, the architecture part coming from the engine
    mapping release_asset_architectures. packages are
    the system tools the app needs on Wayland (wtype, ydotool and
    wl-clipboard) plus the kwriteconfig6 provider used to register the
    Meta+S consuming shortcut. input_group is the group that owns the
    /dev/input and /dev/uinput devices the app reads. service_unit_name is
    the ydotool user unit enabled for the desktop user. The empty Meta+S
    consuming shortcut is the KConfig record shortcuts_file_name,
    shortcut_group_name, shortcut_entry_name, shortcut_action_name and
    shortcut_key_sequence, exactly as the KDE System Settings stores a
    .desktop launch shortcut (docs/spec/vocalinux-setup.md).
    """

    username: str
    home_dir: str
    download_dir: Path
    version: str
    github_repo: str
    asset_name_template: str
    packages: tuple[str, ...]
    input_group: str
    appimage_dir_relative_path: str
    app_config_relative_path: str
    autostart_relative_path: str
    echo_desktop_relative_path: str
    app_config_template_file_name: str
    autostart_template_file_name: str
    echo_desktop_template_file_name: str
    shortcuts_file_name: str
    shortcut_group_name: str
    shortcut_entry_name: str
    shortcut_action_name: str
    shortcut_key_sequence: str
    service_unit_name: str
    package_status_timeout_seconds: int
    package_install_retries: int
    user_file_mode: int
    executable_file_mode: int
    runuser_command: tuple[str, ...]
