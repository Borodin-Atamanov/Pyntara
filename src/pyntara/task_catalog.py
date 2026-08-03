"""Task catalog loading and dialog command generation.

The catalog lives in tasks.yaml, one task per entry. For an install mode the
default task set is exactly the set of tasks whose modes field lists that
mode; there are no separate mode files. This module is the only place that
reads YAML: inst.sh gets plain text and a ready-to-run dialog command, so the
shell never parses the catalog itself.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

# tasks.yaml lives in the repository root, next to the src package. The
# package directory is two levels below the root, so the default catalog path
# is derived from this file instead of the working directory: inst.sh runs
# the command from the cloned repo root, but tests may run from elsewhere.
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "tasks.yaml"


class TaskDef(BaseModel):
    """One task entry from tasks.yaml."""

    name: str
    description: str
    depends: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)


class TaskCatalog:
    """Parsed catalog with task lookup and dependency resolution."""

    def __init__(self, tasks: list[TaskDef]) -> None:
        self._tasks = tasks
        self._by_name = {task.name: task for task in tasks}

    @classmethod
    def from_yaml(cls, path: Path) -> TaskCatalog:
        """Load and validate tasks.yaml, raising ValueError on any problem."""

        if not path.is_file():
            raise ValueError(f"task catalog not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML in {path}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
            raise TypeError(f"task catalog {path} must contain a tasks list")
        try:
            tasks = [TaskDef.model_validate(item) for item in raw["tasks"]]
        except ValidationError as exc:
            raise ValueError(f"invalid task entry in {path}: {exc}") from exc
        return cls(tasks)

    def modes(self) -> list[str]:
        """All install modes referenced by any task, in catalog order."""

        seen: list[str] = []
        for task in self._tasks:
            for mode in task.modes:
                if mode not in seen:
                    seen.append(mode)
        return seen

    def default_tasks(self, mode: str) -> list[str]:
        """Tasks whose modes field lists the given mode, in catalog order."""

        return [task.name for task in self._tasks if mode in task.modes]

    def resolve(self, selected: list[str]) -> list[str]:
        """Expand selected tasks with all transitive dependencies.

        The result lists every selected task and its dependencies in catalog
        order, each exactly once. Dependencies of a task appear before the
        task itself (topological order), which matches the contract: enabling
        a task auto-enables its required dependencies transitively.
        Unknown names are ignored so a stale selection cannot crash the run.
        """

        result: list[str] = []
        for task in self._tasks:
            # A task is included when it is selected itself or when any
            # selected task depends on it, directly or transitively.
            if task.name not in selected and not any(
                task.name in dep_set
                for dep_set in (self._dependencies_of(s) for s in selected)
            ):
                continue
            result.append(task.name)
        return result

    def _dependencies_of(self, name: str) -> set[str]:
        """All transitive dependencies of a task, by name."""

        found: set[str] = set()
        stack = [name]
        while stack:
            current = stack.pop()
            task = self._by_name.get(current)
            if task is None:
                continue
            for dep in task.depends:
                if dep not in found:
                    found.add(dep)
                    stack.append(dep)
        return found

    def dialog_command(
        self,
        mode: str,
        timeout: int,
        result_file: str,
    ) -> str:
        """Build a fully quoted dialog --checklist command for the mode.

        Default-on tasks are checked, every other task in the mode is
        unchecked. The selected item names are written through --output-fd 3
        into result_file. shlex.join quotes every argument, so bash can run
        the command as-is inside script(1) without re-quoting anything.
        """

        defaults = self.default_tasks(mode)
        items: list[str] = []
        for task in self._tasks:
            if mode not in task.modes:
                continue
            items.append(task.name)
            items.append(task.description)
            items.append("on" if task.name in defaults else "off")
        cmd = [
            "dialog",
            "--output-fd",
            "3",
            "--timeout",
            str(timeout),
            "--title",
            f"Select tasks ({mode} mode)",
            "--checklist",
            "Choose tasks to run. Space toggles, Enter confirms.",
            "0",
            "0",
            "0",
        ]
        cmd.extend(items)
        # The result redirect is a shell construct, not a dialog argument.
        # Appending it before shlex.join would quote it as a literal argument
        # (e.g. '3>/tmp/res'), so no redirect would happen and dialog would
        # receive a broken argument list. It is appended after quoting, and
        # shlex.quote keeps a plain path unquoted.
        return shlex.join(cmd) + " 3>" + shlex.quote(result_file)

    def defaults_line(self, mode: str) -> str:
        """The defaults line: 'defaults: name name ...'."""

        return "defaults: " + " ".join(self.default_tasks(mode))

    def dialog_line(self, mode: str, timeout: int, result_file: str) -> str:
        """The dialog line: 'dialog: <command>'."""

        return "dialog: " + self.dialog_command(mode, timeout, result_file)

    def tasks_line(self, selected: list[str]) -> str:
        """The resolved tasks line: 'tasks: name name ...'."""

        return "tasks: " + " ".join(self.resolve(selected))
