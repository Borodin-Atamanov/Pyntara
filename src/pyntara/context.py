from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from .models import AppConfig, InstallModesConfig, TaskDefinition


class SecretsStore(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None:
        ...


@dataclass(frozen=True, slots=True)
class RunContext:
    config: AppConfig
    install_modes: InstallModesConfig
    task_catalog: Mapping[str, TaskDefinition]
    secrets_store: SecretsStore
    logger: logging.Logger
    task_data_dir: Path


def create_run_context(
    *,
    config: AppConfig,
    install_modes: InstallModesConfig,
    task_catalog: Mapping[str, TaskDefinition],
    secrets_store: SecretsStore,
    logger: logging.Logger,
) -> RunContext:
    task_data_dir = config.paths.task_data_dir
    task_data_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(
        config=config,
        install_modes=install_modes,
        task_catalog=MappingProxyType(dict(task_catalog)),
        secrets_store=secrets_store,
        logger=logger,
        task_data_dir=task_data_dir,
    )
