"""[chrome_setup] table: the Google Chrome browser setup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _int_field, _nonempty_string_field


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


def _chrome_setup_table(raw: object) -> ChromeSetupConfig:
    """Validate the [chrome_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[chrome_setup] section is missing or not a table")
    cdp_port = _int_field(raw.get("cdp_port"), "chrome_setup.cdp_port")
    if cdp_port <= 0:
        raise ConfigError("chrome_setup.cdp_port must be positive")
    return ChromeSetupConfig(
        username=_nonempty_string_field(
            raw.get("username"), "chrome_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "chrome_setup.home_dir"
        ),
        settings_repo_url=_nonempty_string_field(
            raw.get("settings_repo_url"), "chrome_setup.settings_repo_url"
        ),
        settings_repo_ref=_nonempty_string_field(
            raw.get("settings_repo_ref"), "chrome_setup.settings_repo_ref"
        ),
        settings_dir=Path(
            _nonempty_string_field(
                raw.get("settings_dir"), "chrome_setup.settings_dir"
            )
        ),
        system_root=Path(
            _nonempty_string_field(
                raw.get("system_root"), "chrome_setup.system_root"
            )
        ),
        apt_source_path=Path(
            _nonempty_string_field(
                raw.get("apt_source_path"), "chrome_setup.apt_source_path"
            )
        ),
        keyring_path=Path(
            _nonempty_string_field(
                raw.get("keyring_path"), "chrome_setup.keyring_path"
            )
        ),
        google_key_url=_nonempty_string_field(
            raw.get("google_key_url"), "chrome_setup.google_key_url"
        ),
        desktop_source_path=Path(
            _nonempty_string_field(
                raw.get("desktop_source_path"),
                "chrome_setup.desktop_source_path",
            )
        ),
        desktop_override_path=Path(
            _nonempty_string_field(
                raw.get("desktop_override_path"),
                "chrome_setup.desktop_override_path",
            )
        ),
        cdp_port=cdp_port,
        cdp_address=_nonempty_string_field(
            raw.get("cdp_address"), "chrome_setup.cdp_address"
        ),
    )
