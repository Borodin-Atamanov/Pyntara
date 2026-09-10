"""[cli_tools] table: the console utility set."""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CliToolsConfig:
    """Console utility set installed by the cli_tools task."""

    packages: tuple[str, ...]
    package_status_timeout_seconds: int
    package_install_retries: int
    package_success_threshold_percent: int
