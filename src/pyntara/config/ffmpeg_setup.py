"""[ffmpeg_setup] table: the ffmpeg install from the Ubuntu archive."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _int_field


@dataclass(frozen=True)
class FfmpegSetupConfig:
    """ffmpeg installed by the ffmpeg_setup task."""

    packages: tuple[str, ...]
    wayrecord_bin_path: Path
    package_status_timeout_seconds: int
    package_install_retries: int


def _ffmpeg_setup_table(raw: object) -> FfmpegSetupConfig:
    """Validate the [ffmpeg_setup] table and build FfmpegSetupConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[ffmpeg_setup] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("ffmpeg_setup.packages must be an array of strings")
    wayrecord_bin_path = raw.get("wayrecord_bin_path")
    if not isinstance(wayrecord_bin_path, str):
        raise ConfigError("ffmpeg_setup.wayrecord_bin_path must be a string")
    return FfmpegSetupConfig(
        packages=tuple(packages),
        wayrecord_bin_path=Path(wayrecord_bin_path),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "ffmpeg_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "ffmpeg_setup.package_install_retries",
        ),
    )
