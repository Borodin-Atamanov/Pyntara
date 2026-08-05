"""Task catalog: single source of truth for task metadata.

The catalog lives in code, one entry per task, with name, description,
dependencies and install-mode membership. The engine computes the default
task set for a mode and expands selections with transitive dependencies.
inst.sh never parses a catalog file, so this module is the only place that
knows the task list.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskDef:
    """One task entry."""

    name: str
    description: str
    depends: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()


TASKS: tuple[TaskDef, ...] = (
    TaskDef(
        name="add_extra_repos",
        description=(
            "Enable extra Ubuntu archive components: universe, restricted, multiverse."
        ),
        modes=("minimal", "server", "desktop"),
    ),
    TaskDef(
        name="users",
        description="Create and configure i, j, k users and required groups.",
        modes=("minimal", "server", "desktop"),
    ),
    TaskDef(
        name="hostname",
        description="Generate and persist random 9-character hostname.",
        modes=("minimal", "server", "desktop"),
    ),
    TaskDef(
        name="passwords",
        description="Derive root and user passwords from salt and hostname.",
        depends=("users", "hostname"),
        modes=("minimal", "server", "desktop"),
    ),
    TaskDef(
        name="cli_tools",
        description="Install curated console utilities: file managers, system and media tools.",
        depends=("add_extra_repos",),
        modes=("minimal", "server", "desktop"),
    ),
    TaskDef(
        name="zram",
        description="Configure aggressive ZRAM by CPU and RAM.",
        modes=("server", "desktop"),
    ),
    TaskDef(
        name="swapfile",
        description="Calculate and configure swapfile from RAM and free disk space.",
        modes=("server", "desktop"),
    ),
    TaskDef(
        name="ssh",
        description="Install and configure SSH service with passwordless login keys.",
        depends=("users",),
        modes=("minimal", "server", "desktop"),
    ),
    TaskDef(
        name="proxy_server",
        description="Local authenticated proxy service with password and port.",
        modes=("server", "desktop"),
    ),
    TaskDef(
        name="proxy_tunnel",
        description="Local tunnel to remote proxy or VPN.",
        depends=("proxy_server",),
        modes=("server", "desktop"),
    ),
    TaskDef(
        name="ntp",
        description="Enable and tune NTP synchronization.",
        modes=("minimal", "server", "desktop"),
    ),
    TaskDef(
        name="power",
        description="Configure power behavior, no suspend on lid close or inactivity.",
        modes=("server", "desktop"),
    ),
    TaskDef(
        name="desktop",
        description="Desktop defaults: Kate, terminal, language indicator, folders.",
        depends=("users",),
        modes=("desktop",),
    ),
    TaskDef(
        name="apps",
        description="Install latest ImageMagick, FFmpeg and scrcpy.",
        depends=("add_extra_repos",),
        modes=("desktop",),
    ),
    TaskDef(
        name="nextdns",
        description="Per-user NextDNS account and system-wide DNS endpoint.",
        depends=("users",),
        modes=("desktop",),
    ),
    TaskDef(
        name="telemetry_setup",
        description="Initial telemetry service setup and first-run queue bootstrap.",
        depends=("users",),
        modes=("server", "desktop"),
    ),
)

MODES: tuple[str, ...] = ("minimal", "server", "desktop")


def validate_mode(mode: str) -> None:
    """Raise ValueError when the mode is not a known install mode."""

    if mode not in MODES:
        raise ValueError(
            f"unknown install mode {mode!r}, expected one of: {', '.join(MODES)}"
        )


def by_name(name: str) -> TaskDef | None:
    """Return the task definition for a name, or None."""

    for task in TASKS:
        if task.name == name:
            return task
    return None


def unknown_tasks(names: list[str]) -> list[str]:
    """Names not present in the catalog, in input order."""

    return [name for name in names if by_name(name) is None]


def default_tasks(mode: str) -> list[str]:
    """Default task set for a mode: tasks whose modes list the mode."""

    return [task.name for task in TASKS if mode in task.modes]


def resolve(selected: list[str]) -> list[str]:
    """Expand selected tasks with all transitive dependencies.

    The result lists every selected task and its dependencies in catalog
    order, each exactly once. Dependencies of a task appear before the task
    itself. Unknown names are ignored; the engine validates the selection
    before resolving it.
    """

    result: list[str] = []
    for task in TASKS:
        # A task is included when it is selected itself or when any selected
        # task depends on it, directly or transitively.
        if task.name in selected:
            result.append(task.name)
            continue
        for selection in selected:
            if task.name in _dependencies_of(selection):
                result.append(task.name)
                break
    return result


def _dependencies_of(name: str) -> set[str]:
    """All transitive dependencies of a task, by name."""

    found: set[str] = set()
    stack = [name]
    while stack:
        current = stack.pop()
        task = by_name(current)
        if task is None:
            continue
        for dep in task.depends:
            if dep not in found:
                found.add(dep)
                stack.append(dep)
    return found
