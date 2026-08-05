"""Task catalog logic: mode defaults, validation and dependency resolution.

The catalog data lives in config.toml under the [[tasks]] section; this
module holds only the logic that operates on it. Every function takes the
catalog as an explicit parameter so it can be tested with any data. inst.sh
never parses the catalog file; the engine is the only place that knows the
task list.
"""

from __future__ import annotations

from pyntara.config import MODES, TaskConfig


def validate_mode(mode: str) -> None:
    """Raise ValueError when the mode is not a known install mode."""

    if mode not in MODES:
        raise ValueError(
            f"unknown install mode {mode!r}, expected one of: {', '.join(MODES)}"
        )


def by_name(name: str, tasks: tuple[TaskConfig, ...]) -> TaskConfig | None:
    """Return the task definition for a name, or None."""

    for task in tasks:
        if task.name == name:
            return task
    return None


def unknown_tasks(names: list[str], tasks: tuple[TaskConfig, ...]) -> list[str]:
    """Names not present in the catalog, in input order."""

    return [name for name in names if by_name(name, tasks) is None]


def default_tasks(mode: str, tasks: tuple[TaskConfig, ...]) -> list[str]:
    """Default task set for a mode: tasks whose modes list the mode."""

    return [task.name for task in tasks if mode in task.modes]


def resolve(selected: list[str], tasks: tuple[TaskConfig, ...]) -> list[str]:
    """Expand selected tasks with all transitive dependencies.

    The result lists every selected task and its dependencies in catalog
    order, each exactly once. Dependencies of a task appear before the task
    itself. Unknown names are ignored; the engine validates the selection
    before resolving it.
    """

    result: list[str] = []
    for task in tasks:
        # A task is included when it is selected itself or when any selected
        # task depends on it, directly or transitively.
        if task.name in selected:
            result.append(task.name)
            continue
        for selection in selected:
            if task.name in _dependencies_of(selection, tasks):
                result.append(task.name)
                break
    return result


def _dependencies_of(name: str, tasks: tuple[TaskConfig, ...]) -> set[str]:
    """All transitive dependencies of a task, by name."""

    found: set[str] = set()
    stack = [name]
    while stack:
        current = stack.pop()
        task = by_name(current, tasks)
        if task is None:
            continue
        for dep in task.depends:
            if dep not in found:
                found.add(dep)
                stack.append(dep)
    return found
