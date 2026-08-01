from __future__ import annotations

import io
from collections.abc import Callable

from pyntara.models import TaskDefinition
from pyntara.task_selector import select_force_mode, select_force_tasks, select_tasks


class _FakeStream(io.StringIO):
    def __init__(self, *, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class _ScriptedKeyReader:
    def __init__(self, keys: list[str | None]) -> None:
        self._keys = keys
        self._index = 0

    def read_key(self, timeout_sec: float) -> str | None:
        _ = timeout_sec
        if self._index >= len(self._keys):
            return None
        value = self._keys[self._index]
        self._index += 1
        return value


class _NeverKeyReader:
    def read_key(self, timeout_sec: float) -> str | None:
        _ = timeout_sec
        return None


def _monotonic_from(values: list[float]) -> Callable[[], float]:
    index = 0
    last = values[-1]

    def _next() -> float:
        nonlocal index, last
        if index < len(values):
            last = values[index]
            index += 1
        return last

    return _next


def _catalog() -> dict[str, TaskDefinition]:
    return {
        "hostname": TaskDefinition.model_validate(
            {
                "name": "hostname",
                "order": 10,
                "description": "Generate host name.",
                "module": "pyntara.tasks.hostname:run",
                "depends_on": [],
                "data_subdir": "hostname",
            }
        ),
        "users": TaskDefinition.model_validate(
            {
                "name": "users",
                "order": 20,
                "description": "Prepare users.",
                "module": "pyntara.tasks.users:run",
                "depends_on": ["hostname"],
                "data_subdir": "users",
            }
        ),
    }


def test_select_tasks_uses_default_when_not_tty() -> None:
    selected = select_tasks(
        task_catalog=_catalog(),
        mode_task_names=["hostname", "users"],
        pre_interaction_timeout_sec=30,
        stdin=_FakeStream(is_tty=False),
        stdout=_FakeStream(is_tty=False),
    )

    assert selected == ["hostname", "users"]


def test_select_tasks_enables_dependencies_when_checked() -> None:
    selected = select_tasks(
        task_catalog=_catalog(),
        mode_task_names=["hostname", "users"],
        pre_interaction_timeout_sec=30,
        stdin=_FakeStream(is_tty=True),
        stdout=_FakeStream(is_tty=True),
        key_reader=_ScriptedKeyReader(["SPACE", "DOWN", "SPACE", "SPACE", "ENTER"]),
        monotonic=_monotonic_from([0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06]),
    )

    assert selected == ["hostname", "users"]


def test_select_tasks_auto_accepts_after_timeout_without_input() -> None:
    selected = select_tasks(
        task_catalog=_catalog(),
        mode_task_names=["hostname", "users"],
        pre_interaction_timeout_sec=2,
        stdin=_FakeStream(is_tty=True),
        stdout=_FakeStream(is_tty=True),
        key_reader=_NeverKeyReader(),
        monotonic=_monotonic_from([0.0, 1.0, 2.1, 2.2]),
    )

    assert selected == ["hostname", "users"]


def test_force_mode_defaults_to_no_on_timeout() -> None:
    result = select_force_mode(
        timeout_sec=2,
        stdin=_FakeStream(is_tty=True),
        stdout=_FakeStream(is_tty=True),
        key_reader=_NeverKeyReader(),
        monotonic=_monotonic_from([0.0, 1.0, 2.1, 2.2]),
    )

    assert result is False


def test_force_mode_can_select_yes() -> None:
    result = select_force_mode(
        timeout_sec=11,
        stdin=_FakeStream(is_tty=True),
        stdout=_FakeStream(is_tty=True),
        key_reader=_ScriptedKeyReader(["RIGHT", "ENTER"]),
        monotonic=_monotonic_from([0.0, 0.01, 0.02, 0.03]),
    )

    assert result is True


def test_force_task_selection_is_independent() -> None:
    selected_force = select_force_tasks(
        selected_task_names=["hostname", "users"],
        task_catalog=_catalog(),
        pre_interaction_timeout_sec=30,
        stdin=_FakeStream(is_tty=True),
        stdout=_FakeStream(is_tty=True),
        key_reader=_ScriptedKeyReader(["SPACE", "DOWN", "ENTER"]),
        monotonic=_monotonic_from([0.0, 0.01, 0.02, 0.03, 0.04]),
    )

    assert selected_force == {"hostname"}
