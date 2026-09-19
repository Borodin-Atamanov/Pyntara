"""Task catalog logic: mode defaults, validation and dependency resolution.

The catalog data lives in pyntara.values.tasks, in the values package; this
module holds only the logic that operates on it. Every function takes the
catalog as an explicit parameter so it can be tested with any data. inst.sh
never parses the catalog; the engine is the only place that knows the task list.
"""

from __future__ import annotations

from pyntara.values.tasks import MODES, TaskSpec


def _folded_mode_name(written: str) -> str:
    """A mode name reduced to the form two spellings of it share.

    A mode is selected by a value a person writes by hand, and the letter
    case and the choice between a hyphen and an underscore are not meant to
    send the run to another mode, so names are compared on this form while
    the declared spelling is what a run applies.
    """

    return written.strip().casefold().replace("-", "_")


def canonical_mode_name(written: str) -> str | None:
    """The declared mode name a written name means, or None.

    The declared spelling is the answer, so the rest of the run carries one
    of the names MODES declares, whatever spelling the caller wrote.
    """

    folded = _folded_mode_name(written)
    for mode in MODES:
        if _folded_mode_name(mode) == folded:
            return mode
    return None


def validate_mode(mode: str) -> None:
    """Raise ValueError when the written name names no declared mode."""

    if canonical_mode_name(mode) is None:
        raise ValueError(
            f"unknown install mode {mode!r}, expected one of: {', '.join(MODES)}"
        )


def by_name(name: str, tasks: tuple[TaskSpec, ...]) -> TaskSpec | None:
    """Return the task definition for a name, or None.

    Names are compared case-insensitively; the catalog name is returned as
    written in the catalog.
    """

    folded = name.casefold()
    for task in tasks:
        if task.name.casefold() == folded:
            return task
    return None


def unknown_tasks(names: list[str], tasks: tuple[TaskSpec, ...]) -> list[str]:
    """Names not present in the catalog, in input order."""

    return [name for name in names if by_name(name, tasks) is None]


def default_tasks(mode: str, tasks: tuple[TaskSpec, ...]) -> list[str]:
    """Default task set for a mode: tasks whose modes list the mode."""

    return [task.name for task in tasks if mode in task.modes]


def resolve(selected: list[str], tasks: tuple[TaskSpec, ...]) -> list[str]:
    """Expand selected tasks with all transitive dependencies.

    The result lists every selected task and its dependencies in catalog
    order, each exactly once, under the canonical catalog names. Selection
    names are matched case-insensitively. Dependencies of a task appear
    before the task itself. Unknown names are ignored; the engine validates
    the selection before resolving it.
    """

    selected_folded = {name.casefold() for name in selected}
    result: list[str] = []
    for task in tasks:
        # A task is included when it is selected itself or when any selected
        # task depends on it, directly or transitively.
        if task.name.casefold() in selected_folded:
            result.append(task.name)
            continue
        for selection in selected:
            if task.name in _dependencies_of(selection, tasks):
                result.append(task.name)
                break
    return result


def _dependencies_of(name: str, tasks: tuple[TaskSpec, ...]) -> set[str]:
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
