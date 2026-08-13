"""[[tasks]] section: the task catalog."""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import MODES, ConfigError


@dataclass(frozen=True)
class TaskConfig:
    """One task entry from the [[tasks]] section of config.toml."""

    name: str
    description: str
    depends: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()


def _tasks_table(raw: object) -> tuple[TaskConfig, ...]:
    """Validate the [[tasks]] section and build the task catalog.

    The catalog is non-empty; names are unique Python identifiers; every
    dependency names a task listed earlier in the file, which also rules out
    dependency cycles and keeps default task sets ordered; modes are known
    install modes without duplicates.
    """

    if not isinstance(raw, list):
        raise ConfigError("[tasks] section is missing or not an array of tables")
    result: list[TaskConfig] = []
    seen_names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError("[tasks] entries must be tables")
        name = entry.get("name")
        if not isinstance(name, str) or not name or not name.isidentifier():
            raise ConfigError("[tasks] task name must be a non-empty identifier")
        if name in seen_names:
            raise ConfigError(f"[tasks] duplicate task name: {name}")
        seen_names.add(name)
        description = entry.get("description")
        if not isinstance(description, str):
            raise ConfigError(f"[tasks] task {name}: description must be a string")
        depends_raw = entry.get("depends", [])
        if not isinstance(depends_raw, list) or not all(
            isinstance(dep, str) for dep in depends_raw
        ):
            raise ConfigError(
                f"[tasks] task {name}: depends must be an array of strings"
            )
        known_names = {task.name for task in result}
        for dep in depends_raw:
            if dep not in known_names:
                raise ConfigError(
                    f"[tasks] task {name}: dependency {dep!r} must be listed earlier"
                )
        modes_raw = entry.get("modes")
        if not isinstance(modes_raw, list) or not modes_raw:
            raise ConfigError(f"[tasks] task {name}: modes must be a non-empty array")
        if not all(isinstance(mode, str) for mode in modes_raw):
            raise ConfigError(f"[tasks] task {name}: modes must be strings")
        for mode in modes_raw:
            if mode not in MODES:
                raise ConfigError(
                    f"[tasks] task {name}: unknown install mode {mode!r}"
                )
        if len(set(modes_raw)) != len(modes_raw):
            raise ConfigError(f"[tasks] task {name}: duplicate mode entries")
        result.append(
            TaskConfig(
                name=name,
                description=description,
                depends=tuple(depends_raw),
                modes=tuple(modes_raw),
            )
        )
    if not result:
        raise ConfigError("[tasks] section must contain at least one task")
    return tuple(result)
