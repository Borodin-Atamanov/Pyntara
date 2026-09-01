"""[imagemagick_setup] table: the modern ImageMagick install."""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import ConfigError, _int_field


@dataclass(frozen=True)
class ImagemagickSetupConfig:
    """ImageMagick installed by the imagemagick_setup task."""

    packages: tuple[str, ...]
    package_status_timeout_seconds: int
    package_install_retries: int


def _imagemagick_setup_table(raw: object) -> ImagemagickSetupConfig:
    """Validate the [imagemagick_setup] table and build ImagemagickSetupConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[imagemagick_setup] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("imagemagick_setup.packages must be an array of strings")
    return ImagemagickSetupConfig(
        packages=tuple(packages),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "imagemagick_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "imagemagick_setup.package_install_retries",
        ),
    )
