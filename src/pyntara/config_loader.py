from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .models import AppConfig, InstallModesConfig, TaskDefinition


@dataclass(frozen=True, slots=True)
class LoadedConfiguration:
    app_config: AppConfig
    task_catalog: dict[str, TaskDefinition]
    install_modes: InstallModesConfig


def load_runtime_configuration(
    *,
    config_path: Path,
    tasks_path: Path,
    install_modes_path: Path,
    cli_overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> LoadedConfiguration:
    file_config = _read_yaml_file(config_path)
    env_overrides = _parse_env_overrides(env=env)
    merged = _deep_merge(_default_config_data(), file_config)
    merged = _deep_merge(merged, env_overrides)
    if cli_overrides is not None:
        merged = _deep_merge(merged, dict(cli_overrides))

    app_config = AppConfig.model_validate(merged)
    task_catalog = _load_task_catalog(tasks_path)
    install_modes = InstallModesConfig.model_validate(_read_yaml_file(install_modes_path))
    _validate_catalog(task_catalog=task_catalog, install_modes=install_modes)
    return LoadedConfiguration(
        app_config=app_config,
        task_catalog=task_catalog,
        install_modes=install_modes,
    )


def _default_config_data() -> dict[str, Any]:
    return AppConfig().model_dump(mode="python")


def _read_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as config_file:
        parsed: Any = yaml.safe_load(config_file) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"YAML file {path} must contain a mapping root.")
    return dict(parsed)


def _load_task_catalog(tasks_path: Path) -> dict[str, TaskDefinition]:
    payload = _read_yaml_file(tasks_path)
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("tasks.yaml must contain a 'tasks' list.")

    catalog: dict[str, TaskDefinition] = {}
    for item in raw_tasks:
        task_def = TaskDefinition.model_validate(item)
        if task_def.name in catalog:
            raise ValueError(f"Duplicate task name in catalog: {task_def.name}")
        if task_def.data_subdir is None:
            task_def = task_def.model_copy(update={"data_subdir": task_def.name})
        catalog[task_def.name] = task_def
    return catalog


def _validate_catalog(
    *,
    task_catalog: Mapping[str, TaskDefinition],
    install_modes: InstallModesConfig,
) -> None:
    for mode_name, mode_tasks in (
        ("minimal", install_modes.minimal),
        ("server", install_modes.server),
        ("desktop", install_modes.desktop),
    ):
        for task_name in mode_tasks:
            if task_name not in task_catalog:
                raise ValueError(f"Unknown task '{task_name}' in install mode '{mode_name}'.")

    for task_name, task_def in task_catalog.items():
        for dependency in task_def.depends_on:
            if dependency not in task_catalog:
                raise ValueError(
                    f"Unknown dependency '{dependency}' declared by task '{task_name}'."
                )


def _parse_env_overrides(*, env: Mapping[str, str] | None) -> dict[str, Any]:
    source = env if env is not None else os.environ
    overrides: dict[str, Any] = {}
    prefix = "PYNTARA_"
    for raw_key, raw_value in source.items():
        if not raw_key.startswith(prefix):
            continue
        path_tokens = raw_key[len(prefix) :].lower().split("__")
        _insert_nested(overrides, path_tokens, _coerce_scalar(raw_value))
    return overrides


def _insert_nested(target: dict[str, Any], path_tokens: list[str], value: Any) -> None:
    cursor: dict[str, Any] = target
    for token in path_tokens[:-1]:
        if token not in cursor:
            cursor[token] = {}
        next_value = cursor[token]
        if not isinstance(next_value, dict):
            raise ValueError(f"Invalid environment override path segment: {token}")
        cursor = next_value
    cursor[path_tokens[-1]] = value


def _coerce_scalar(raw_value: str) -> Any:
    if raw_value.lower() in {"true", "false"}:
        return raw_value.lower() == "true"
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return raw_value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged
