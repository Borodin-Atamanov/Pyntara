"""Unit tests for the in-code task catalog.

The catalog is the single source of truth for task metadata. inst.sh never
parses a catalog file: the engine owns defaults, validation and dependency
resolution. Tests use the real catalog because it is data, not external
state.
"""

from __future__ import annotations

import pytest

from pyntara import task_catalog
from pyntara.task_catalog import TASKS, TaskDef


def test_modes_are_the_three_install_modes() -> None:
    assert task_catalog.MODES == ("minimal", "server", "desktop")


def test_default_tasks_match_mode_membership_exactly() -> None:
    for mode in task_catalog.MODES:
        expected = [task.name for task in TASKS if mode in task.modes]
        assert task_catalog.default_tasks(mode) == expected


def test_minimal_mode_defaults_exclude_desktop_tasks() -> None:
    defaults = task_catalog.default_tasks("minimal")
    assert "desktop" not in defaults
    assert "apps" not in defaults


def test_resolve_adds_transitive_dependencies() -> None:
    # Selecting proxy_tunnel must pull in its dependency proxy_server.
    assert task_catalog.resolve(["proxy_tunnel"]) == ["proxy_server", "proxy_tunnel"]


def test_resolve_puts_dependencies_before_the_task() -> None:
    assert task_catalog.resolve(["passwords"]) == ["users", "hostname", "passwords"]


def test_resolve_keeps_catalog_order_and_deduplicates() -> None:
    result = task_catalog.resolve(["hostname", "passwords", "hostname"])
    assert result == ["users", "hostname", "passwords"]


def test_resolve_ignores_unknown_names() -> None:
    # The engine validates selections before resolving; resolve stays lenient.
    assert task_catalog.resolve(["nope"]) == []


def test_unknown_tasks_reports_unknown_names() -> None:
    assert task_catalog.unknown_tasks(["nope", "users"]) == ["nope"]


def test_validate_mode_accepts_known_modes() -> None:
    for mode in task_catalog.MODES:
        task_catalog.validate_mode(mode)


def test_validate_mode_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown install mode"):
        task_catalog.validate_mode("fancy")


def test_catalog_names_are_unique() -> None:
    names = [task.name for task in TASKS]
    assert len(names) == len(set(names))


def test_dependencies_refer_to_known_tasks() -> None:
    known = {task.name for task in TASKS}
    for task in TASKS:
        for dep in task.depends:
            assert dep in known


def test_task_def_is_frozen() -> None:
    task = TaskDef(name="x", description="X")
    with pytest.raises(AttributeError):
        task.name = "y"  # type: ignore[misc]
