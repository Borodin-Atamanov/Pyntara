from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import cast

from .models import TaskDefinition
from .task_protocol import TaskCallable


@dataclass(frozen=True, slots=True)
class RegisteredTask:
    definition: TaskDefinition
    runner: TaskCallable


class TaskRegistry:
    def __init__(self, *, task_catalog: Mapping[str, TaskDefinition]) -> None:
        self._task_catalog = task_catalog

    def get(self, task_name: str) -> RegisteredTask:
        if task_name not in self._task_catalog:
            raise KeyError(f"Task '{task_name}' is not defined in catalog.")
        definition = self._task_catalog[task_name]
        return RegisteredTask(definition=definition, runner=_import_task_runner(definition.module))


def _import_task_runner(module_ref: str) -> TaskCallable:
    module_name, separator, function_name = module_ref.partition(":")
    if separator == "" or function_name == "":
        raise ValueError(f"Invalid task module reference: {module_ref}")

    module = import_module(module_name)
    candidate = getattr(module, function_name, None)
    if candidate is None or not callable(candidate):
        raise ValueError(f"Task callable '{module_ref}' does not exist or is not callable.")

    signature = inspect.signature(candidate)
    if "ctx" not in signature.parameters:
        raise ValueError(f"Task callable '{module_ref}' must accept 'ctx'.")

    return cast(TaskCallable, candidate)
