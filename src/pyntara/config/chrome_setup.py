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
    desktop_source_path with the CDP flags appended to every Exec line: the
    debug port cdp_port bound to the loopback address cdp_address
    (docs/spec/chrome-setup.md).
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
    cdp_port: int
    cdp_address: str
