"""[ffmpeg_setup] table: the ffmpeg install from the Ubuntu archive."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FfmpegSetupConfig:
    """ffmpeg installed by the ffmpeg_setup task."""

    packages: tuple[str, ...]
    wayrecord_bin_path: Path
    wayrecord_desktop_path: Path
    wayrecord_file_mode: int
    package_status_timeout_seconds: int
    package_install_retries: int
