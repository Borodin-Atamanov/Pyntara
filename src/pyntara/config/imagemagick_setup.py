"""[imagemagick_setup] table: the modern ImageMagick install."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImagemagickSetupConfig:
    """ImageMagick installed and tuned by the imagemagick_setup task."""

    packages: tuple[str, ...]
    policy_path: Path
    package_status_timeout_seconds: int
    package_install_retries: int
