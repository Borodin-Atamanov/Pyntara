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
    npm_install_timeout_seconds: int
