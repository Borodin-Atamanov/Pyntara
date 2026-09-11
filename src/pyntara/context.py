"""Runtime context passed to every task.

The entry point builds this object once and hands it to the runner; tasks
receive it as their only argument and never read the environment or config
files directly. The dataclass is frozen so tasks cannot mutate shared state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyntara.config import Config


@dataclass(frozen=True)
class Context:
    """Everything a task may need during provisioning.

    The runner fills task_name with the name the catalog gives the task it
    is about to call, so a task module never writes its own name
    (docs/contracts/task-model.md). A context that is not running a task
    carries the empty string.
    """

    install_mode: str
    vault_password: str | None
    vault_source: str | None
    force_tasks: frozenset[str]
    repo_root: Path
    task_data_root: Path
    skip_apt_update: bool
    config: Config
    task_name: str = ""
