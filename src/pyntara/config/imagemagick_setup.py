"""[imagemagick_setup] table: the modern ImageMagick install."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _int_field


@dataclass(frozen=True)
class ImagemagickSetupConfig:
    """ImageMagick installed and tuned by the imagemagick_setup task."""

    packages: tuple[str, ...]
    policy_path: Path
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
    policy_path = raw.get("policy_path")
    if not isinstance(policy_path, str):
        raise ConfigError("imagemagick_setup.policy_path must be a string")
    return ImagemagickSetupConfig(
        packages=tuple(packages),
        policy_path=Path(policy_path),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "imagemagick_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "imagemagick_setup.package_install_retries",
        ),
    )
