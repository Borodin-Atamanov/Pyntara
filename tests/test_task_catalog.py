"""Unit tests for the task catalog and its dialog command generation.

The catalog is the single source of truth for task metadata; inst.sh never
parses YAML, so the Python side owns loading, validation, defaults and
dependency resolution. Tests use small in-memory catalogs, never the real
tasks.yaml, so they stay independent of catalog content.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pyntara.task_catalog import TaskCatalog, TaskDef


def _catalog() -> TaskCatalog:
    """A small catalog with dependencies and mode membership."""

    return TaskCatalog(
        [
            TaskDef(name="a", description="Task A", depends=[], modes=["minimal", "server"]),
            TaskDef(name="b", description="Task B", depends=["a"], modes=["minimal"]),
            TaskDef(name="c", description="Task C", depends=["b"], modes=["server"]),
        ]
    )


def test_default_tasks_follow_mode_membership() -> None:
    # The default set for a mode is exactly the tasks listing that mode.
    cat = _catalog()
    assert cat.default_tasks("minimal") == ["a", "b"]
    assert cat.default_tasks("server") == ["a", "c"]


def test_resolve_adds_transitive_dependencies() -> None:
    # Selecting c must pull in b and then a, in catalog order, once each.
    cat = _catalog()
    assert cat.resolve(["c"]) == ["a", "b", "c"]


def test_resolve_keeps_catalog_order_and_deduplicates() -> None:
    # Repeating a name or selecting out of order must not duplicate or reorder.
    cat = _catalog()
    assert cat.resolve(["b", "a", "b"]) == ["a", "b"]


def test_resolve_ignores_unknown_names() -> None:
    # A stale selection with unknown names must not crash the resolution.
    cat = _catalog()
    assert cat.resolve(["nope", "c"]) == ["a", "b", "c"]


def test_dialog_command_marks_defaults_on() -> None:
    # Default tasks must be 'on', others 'off', and the command must quote
    # every argument so bash can run it via script(1) as-is.
    cat = _catalog()
    cmd = cat.dialog_command("minimal", 30, "/tmp/res")
    assert "--timeout" in cmd and "30" in cmd
    assert "a" in cmd and "Task A" in cmd and "on" in cmd
    assert "b" in cmd and "Task B" in cmd and "on" in cmd
    # Every argument that could carry spaces is inside single quotes.
    assert "'Task A'" in cmd and "'Task B'" in cmd


def test_dialog_command_redirect_is_not_quoted() -> None:
    # The 3>result-file redirect is a shell construct and must stay unquoted
    # after shlex.join; quoting it would turn it into a literal dialog
    # argument and no redirect would happen.
    cat = _catalog()
    cmd = cat.dialog_command("minimal", 30, "/tmp/res")
    assert cmd.endswith(" 3>/tmp/res")
    assert "'3>/tmp/res'" not in cmd


def test_dialog_command_excludes_other_modes() -> None:
    # Tasks not in the mode must not appear in its dialog command.
    cat = _catalog()
    cmd = cat.dialog_command("minimal", 30, "/tmp/res")
    assert "Task C" not in cmd


def test_lines_protocol() -> None:
    # The defaults/dialog/tasks lines follow the documented text protocol.
    cat = _catalog()
    assert cat.defaults_line("minimal") == "defaults: a b"
    assert cat.dialog_line("minimal", 30, "/tmp/res").startswith("dialog: ")
    assert cat.tasks_line(["c"]) == "tasks: a b c"


def test_from_yaml_rejects_missing_file(tmp_path: pytest.TempPathFactory) -> None:
    with pytest.raises(ValueError, match="not found"):
        TaskCatalog.from_yaml(tmp_path / "nope.yaml")


def test_from_yaml_rejects_bad_yaml(tmp_path: pytest.TempPathFactory) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("tasks: [unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid YAML"):
        TaskCatalog.from_yaml(path)


def test_from_yaml_rejects_invalid_entry(tmp_path: pytest.TempPathFactory) -> None:
    # A task with a wrong field type is caught by pydantic.
    path = tmp_path / "bad.yaml"
    path.write_text("tasks:\n  - name: 42\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid task entry"):
        TaskCatalog.from_yaml(path)


def test_from_yaml_rejects_missing_tasks_list(tmp_path: pytest.TempPathFactory) -> None:
    # A catalog without a tasks list is rejected as a structural error.
    path = tmp_path / "bad.yaml"
    path.write_text("something: else\n", encoding="utf-8")
    with pytest.raises(TypeError, match="tasks list"):
        TaskCatalog.from_yaml(path)


def test_task_def_validates_modes_list() -> None:
    # modes must be a list of strings; a wrong type is caught by pydantic.
    with pytest.raises(ValidationError):
        TaskDef(name="x", description="X", modes="minimal")  # type: ignore[arg-type]
