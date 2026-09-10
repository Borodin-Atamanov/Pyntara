"""[vocalinux_setup] table: the Vocalinux voice dictation app."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VocalinuxSetupConfig:
    """Vocalinux dictation installed for the desktop user.

    username and home_dir identify the desktop user who runs Vocalinux;
    the AppImage, the app config and the autostart entry are derived under
    that home (install directory home_dir/.local/share/vocalinux/appimage,
    app config home_dir/.config/vocalinux/config.json, autostart entry
    home_dir/.config/autostart/vocalinux.desktop). download_dir is the
    root cache that keeps the AppImage of the pinned version. version is
    the pinned Vocalinux release the task installs, without a leading v;
    the release tag and the asset name are derived from it. packages are
    the system tools the app needs on Wayland (wtype, ydotool and
    wl-clipboard) plus the kwriteconfig6 provider used to register the
    Meta+S consuming shortcut. input_group is the group that owns the
    /dev/input and /dev/uinput devices the app reads. service_unit_name is
    the ydotool user unit enabled for the desktop user
    (docs/spec/vocalinux-setup.md).
    """

    username: str
    home_dir: str
    download_dir: Path
    version: str
    packages: tuple[str, ...]
    input_group: str
    service_unit_name: str
    package_status_timeout_seconds: int
    package_install_retries: int
    user_file_mode: int
    executable_file_mode: int
