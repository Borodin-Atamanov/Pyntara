"""Configuration loading from config.toml.

The file at the repository root is the single source of truth for the
Python part of the engine. A missing or invalid file stops the run: there
are no defaults (architecture contract section 3). The composition root
loads the config once and hands it to every task through Context.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when config.toml is missing, unreadable or invalid."""


@dataclass(frozen=True)
class EngineConfig:
    """Engine-wide runtime values from the [engine] table."""

    task_data_root: Path
    notice_timeout: int


@dataclass(frozen=True)
class CliToolsConfig:
    """Console utility set installed by the cli_tools task."""

    packages: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    """Validated content of config.toml."""

    engine: EngineConfig
    cli_tools: CliToolsConfig


def _engine_table(raw: object) -> EngineConfig:
    """Validate the [engine] table and build EngineConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[engine] section is missing or not a table")
    task_data_root = raw.get("task_data_root")
    if not isinstance(task_data_root, str):
        raise ConfigError("engine.task_data_root must be a string")
    notice_timeout = raw.get("notice_timeout")
    # bool is a subclass of int in Python, so it must be excluded explicitly.
    if not isinstance(notice_timeout, int) or isinstance(notice_timeout, bool):
        raise ConfigError("engine.notice_timeout must be an integer")
    return EngineConfig(
        task_data_root=Path(task_data_root),
        notice_timeout=notice_timeout,
    )


def _cli_tools_table(raw: object) -> CliToolsConfig:
    """Validate the [cli_tools] table and build CliToolsConfig."""

    if not isinstance(raw, dict):
        raise ConfigError("[cli_tools] section is missing or not a table")
    packages = raw.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(package, str) for package in packages
    ):
        raise ConfigError("cli_tools.packages must be an array of strings")
    return CliToolsConfig(packages=tuple(packages))


def load_config(path: Path) -> Config:
    """Read and validate config.toml. Raises ConfigError on any problem."""

    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    return Config(
        engine=_engine_table(data.get("engine")),
        cli_tools=_cli_tools_table(data.get("cli_tools")),
    )
