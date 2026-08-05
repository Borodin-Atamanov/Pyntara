"""Unit tests for the task catalog logic.

The catalog data lives in config.toml under the [[tasks]] section; this
module tests the logic that operates on it. The real config file is loaded
so the tests cover the actual task set, dependencies and mode membership.
inst.sh never parses the catalog file: the engine owns defaults, validation
and dependency resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara import task_catalog
from pyntara.config import MODES, TaskConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS = load_config(REPO_ROOT / "config.toml").tasks


def test_modes_are_the_three_install_modes() -> None:
    assert MODES == ("minimal", "server", "desktop")


def test_default_tasks_match_mode_membership_exactly() -> None:
    for mode in MODES:
        expected = [task.name for task in TASKS if mode in task.modes]
        assert task_catalog.default_tasks(mode, TASKS) == expected


def test_minimal_mode_defaults_exclude_desktop_tasks() -> None:
    defaults = task_catalog.default_tasks("minimal", TASKS)
    assert "desktop" not in defaults
    assert "apps" not in defaults


def test_add_extra_repos_is_first_in_every_mode() -> None:
    # add_extra_repos must run before any package install, so it leads the
    # default task set of every mode.
    for mode in MODES:
        defaults = task_catalog.default_tasks(mode, TASKS)
        assert defaults[0] == "add_extra_repos"


def test_resolve_cli_tools_pulls_add_extra_repos() -> None:
    # Selecting cli_tools alone must enable add_extra_repos first, because
    # its packages live in universe and multiverse.
    assert task_catalog.resolve(["cli_tools"], TASKS) == [
        "add_extra_repos",
        "cli_tools",
    ]


def test_resolve_apps_pulls_add_extra_repos() -> None:
    assert task_catalog.resolve(["apps"], TASKS) == ["add_extra_repos", "apps"]


def test_resolve_adds_transitive_dependencies() -> None:
    # Selecting proxy_tunnel must pull in its dependency proxy_server.
    assert task_catalog.resolve(["proxy_tunnel"], TASKS) == [
        "proxy_server",
        "proxy_tunnel",
    ]


def test_resolve_puts_dependencies_before_the_task() -> None:
    assert task_catalog.resolve(["passwords"], TASKS) == [
        "users",
        "hostname",
        "passwords",
    ]


def test_resolve_keeps_catalog_order_and_deduplicates() -> None:
    result = task_catalog.resolve(["hostname", "passwords", "hostname"], TASKS)
    assert result == ["users", "hostname", "passwords"]


def test_resolve_ignores_unknown_names() -> None:
    # The engine validates selections before resolving; resolve stays lenient.
    assert task_catalog.resolve(["nope"], TASKS) == []


def test_unknown_tasks_reports_unknown_names() -> None:
    assert task_catalog.unknown_tasks(["nope", "users"], TASKS) == ["nope"]


def test_validate_mode_accepts_known_modes() -> None:
    for mode in MODES:
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


def test_task_config_is_frozen() -> None:
    task = TaskConfig(name="x", description="X")
    with pytest.raises(AttributeError):
        task.name = "y"  # type: ignore[misc]
