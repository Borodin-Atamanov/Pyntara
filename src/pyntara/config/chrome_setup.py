"""[chrome_setup] table: the Google Chrome browser setup."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChromeSetupConfig:
    """Google Chrome installed and configured for the desktop user.

    username and home_dir identify the desktop user whose standard Chrome
    profile (home_dir plus profile_dir_relative_path) receives the browser
    settings from preferences_relative_path of the repository. package_name
    is the apt package of the browser, process_name the name pgrep sees for
    a running main process. settings_repo_url and settings_repo_ref name the
    git repository of browser settings, cloned into settings_dir by
    settings_clone_command, updated by settings_fetch_command,
    settings_revision_command and settings_reset_command; its tree at
    settings_system_tree_relative_path (the machine policy and the external
    extension files) is deployed under system_root with the relative paths
    preserved, so "/" in production. The official Google apt repository is
    registered as the deb822 source at apt_source_path, rendered from the
    template apt_source_template_file_name with the keyring path, whose
    keyring lives at keyring_path and is downloaded from google_key_url
    when missing and dearmored by keyring_dearmor_command in a temporary
    directory named by keyring_temp_dir_prefix.
    desktop_override_path receives the packaged desktop entry at
    desktop_source_path with launch_flags appended to every Exec line: the
    local SOCKS5 proxy of the three_x_ui_xray_setup section when it
    listens, the profile mirror at profile_mirror_path, and the debug port
    cdp_port bound to the loopback address cdp_address; a flag whose
    placeholder has no value is left out
    (docs/spec/chrome-setup.md). profile_mirror_path is a bind mount of
    home_dir/.config/google-chrome, because branded Chrome refuses the CDP
    listener on the default data directory; the oneshot unit
    mount_service_unit_name restores the mount at every boot through
    mount_reload_command and mount_enable_command, and mount_check_command
    confirms the mount. The taskbar pinning uses appletsrc_file_name and
    appletsrc_relative_path of the desktop user, taskbar_plugin_names as
    the applet plugins whose launcher list receives the button and
    panel_launcher_id as the pinned launcher, and menu_refresh_command
    rebuilds the menu cache. file_mode is the mode of every deployed
    configuration and desktop file. process_check_command and
    settings_revision_command take {process_name} and {revision}.
    """

    username: str
    home_dir: str
    package_name: str
    process_name: str
    appletsrc_file_name: str
    appletsrc_relative_path: str
    appletsrc_launchers_key: str
    taskbar_plugin_names: tuple[str, ...]
    panel_launcher_id: str
    panel_restart_command: tuple[str, ...]
    settings_repo_url: str
    settings_repo_ref: str
    settings_dir: Path
    settings_system_tree_relative_path: str
    preferences_relative_path: str
    profile_dir_relative_path: str
    keyring_temp_dir_prefix: str
    apt_source_template_file_name: str
    launch_flags: tuple[str, ...]
    keyring_dearmor_command: tuple[str, ...]
    settings_clone_command: tuple[str, ...]
    settings_fetch_command: tuple[str, ...]
    settings_revision_command: tuple[str, ...]
    settings_reset_command: tuple[str, ...]
    process_check_command: tuple[str, ...]
    mount_check_command: tuple[str, ...]
    mount_reload_command: tuple[str, ...]
    mount_enable_command: tuple[str, ...]
    menu_refresh_command: tuple[str, ...]
    system_root: Path
    apt_source_path: Path
    keyring_path: Path
    google_key_url: str
    desktop_source_path: Path
    desktop_override_path: Path
    profile_mirror_path: Path
    mount_service_unit_name: str
    mount_unit_template_file_name: str
    cdp_port: int
    cdp_address: str
    file_mode: int
