"""[vocalinux_setup] table: the Vocalinux voice dictation app."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _int_field, _nonempty_string_field, _string_list


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


def _vocalinux_setup_table(raw: object) -> VocalinuxSetupConfig:
    """Validate the [vocalinux_setup] table and build the config."""

    if not isinstance(raw, dict):
        raise ConfigError("[vocalinux_setup] section is missing or not a table")
    return VocalinuxSetupConfig(
        username=_nonempty_string_field(
            raw.get("username"), "vocalinux_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "vocalinux_setup.home_dir"
        ),
        download_dir=Path(
            _nonempty_string_field(
                raw.get("download_dir"), "vocalinux_setup.download_dir"
            )
        ),
        version=_nonempty_string_field(
            raw.get("version"), "vocalinux_setup.version"
        ),
        packages=_string_list(raw.get("packages"), "vocalinux_setup.packages"),
        input_group=_nonempty_string_field(
            raw.get("input_group"), "vocalinux_setup.input_group"
        ),
        service_unit_name=_nonempty_string_field(
            raw.get("service_unit_name"), "vocalinux_setup.service_unit_name"
        ),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "vocalinux_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "vocalinux_setup.package_install_retries",
        ),
    )
