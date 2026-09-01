"""[ffmpeg_setup] table: the ffmpeg install from the Ubuntu archive."""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import ConfigError, _int_field


@dataclass(frozen=True)
class FfmpegSetupConfig:
    """ffmpeg installed by the ffmpeg_setup task."""

    packages: tuple[str, ...]
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
    return FfmpegSetupConfig(
        packages=tuple(packages),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "ffmpeg_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "ffmpeg_setup.package_install_retries",
        ),
    )
