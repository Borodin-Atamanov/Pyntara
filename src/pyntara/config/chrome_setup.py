"""[chrome_setup] table: the Google Chrome browser setup."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChromeSetupConfig:
    """Google Chrome installed and configured for the desktop user.

    username and home_dir identify the desktop user whose standard Chrome
    profile (home_dir/.config/google-chrome/Default/Preferences) receives
    the browser settings. settings_repo_url and settings_repo_ref name the
    git repository of browser settings, cloned into settings_dir; its
    system/ tree (the machine policy and the external extension files) is
    deployed under system_root with the relative paths preserved, so "/" in
    production. The official Google apt repository is registered as the
    deb822 source at apt_source_path, whose Signed-By keyring lives at
    keyring_path and is downloaded from google_key_url when missing.
    desktop_override_path receives the packaged desktop entry at
    desktop_source_path with the launch flags appended to every Exec line:
    the local SOCKS5 proxy of the three_x_ui_xray_setup section when it
    listens, the profile mirror at profile_mirror_path, and the debug port
    cdp_port bound to the loopback address cdp_address
    (docs/spec/chrome-setup.md). profile_mirror_path is a bind mount of
    home_dir/.config/google-chrome, because branded Chrome refuses the CDP
    listener on the default data directory; the oneshot unit
    mount_service_unit_name restores the mount at every boot. file_mode is
    the mode of every deployed configuration and desktop file.
    """

    username: str
    home_dir: str
    settings_repo_url: str
    settings_repo_ref: str
    settings_dir: Path
    system_root: Path
    apt_source_path: Path
    keyring_path: Path
    google_key_url: str
    desktop_source_path: Path
    desktop_override_path: Path
    profile_mirror_path: Path
    mount_service_unit_name: str
    cdp_port: int
    cdp_address: str
    file_mode: int
