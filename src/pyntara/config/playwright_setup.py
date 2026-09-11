"""[playwright_setup] table: the playwright-cli browser control tool."""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaywrightSetupConfig:
    """playwright-cli installed for the desktop user by the playwright_setup task."""

    username: str
    home_dir: str
    packages: tuple[str, ...]
    package_status_timeout_seconds: int
    package_install_retries: int
    cli_package: str
    user_prefix_relative_path: str
    cli_bin_relative_path: str
    runuser_command: tuple[str, ...]
    cli_version_command: tuple[str, ...]
    npm_install_command: tuple[str, ...]
    npm_install_timeout_seconds: int
