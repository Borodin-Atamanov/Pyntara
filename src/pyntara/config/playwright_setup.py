"""[playwright_setup] table: the playwright-cli browser control tool."""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import ConfigError, _int_field, _nonempty_string_field


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


def _playwright_setup_table(raw: object) -> PlaywrightSetupConfig:
    """Validate the [playwright_setup] table and build PlaywrightSetupConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[playwright_setup] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("playwright_setup.packages must be an array of strings")
    return PlaywrightSetupConfig(
        username=_nonempty_string_field(
            raw.get("username"), "playwright_setup.username"
        ),
        home_dir=_nonempty_string_field(
            raw.get("home_dir"), "playwright_setup.home_dir"
        ),
        packages=tuple(packages),
        package_status_timeout_seconds=_int_field(
            raw.get("package_status_timeout_seconds"),
            "playwright_setup.package_status_timeout_seconds",
        ),
        package_install_retries=_int_field(
            raw.get("package_install_retries"),
            "playwright_setup.package_install_retries",
        ),
        cli_package=_nonempty_string_field(
            raw.get("cli_package"), "playwright_setup.cli_package"
        ),
        npm_install_timeout_seconds=_int_field(
            raw.get("npm_install_timeout_seconds"),
            "playwright_setup.npm_install_timeout_seconds",
        ),
    )
