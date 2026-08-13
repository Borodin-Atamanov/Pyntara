"""Unit tests for the task catalog logic.

The catalog data lives in config.toml under the [[tasks]] section; this
module tests the logic that operates on it. The mechanics of dependency
resolution are tested on a small synthetic catalog so they never depend on
specific task names. Data checks against the real config only reference
implemented tasks: future tasks are expected to change and must not be
mentioned by name in tests. inst.sh never parses the catalog file: the
engine owns defaults, validation and dependency resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyntara import task_catalog
from pyntara.config import MODES, TaskConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS = load_config(REPO_ROOT / "config").tasks


# A synthetic three-task chain with one transitive dependency. Mechanics
# tests use it so they stay valid regardless of which tasks exist or are
# implemented; only the two implemented tasks are referenced by name in
# data tests. All three tasks belong to every mode.
_ALL_MODES = ("minimal", "server", "desktop")
SYNTHETIC_TASKS: tuple[TaskConfig, ...] = (
    TaskConfig(name="a", description="A.", modes=_ALL_MODES),
    TaskConfig(name="b", description="B.", depends=("a",), modes=_ALL_MODES),
    TaskConfig(name="c", description="C.", depends=("b",), modes=_ALL_MODES),
)


def test_modes_are_the_three_install_modes() -> None:
    assert MODES == ("minimal", "server", "desktop")


def test_default_tasks_match_mode_membership_exactly() -> None:
    for mode in MODES:
        expected = [task.name for task in TASKS if mode in task.modes]
        assert task_catalog.default_tasks(mode, TASKS) == expected


def test_default_tasks_use_configured_mode_membership() -> None:
    # A task listed only for desktop must never appear in minimal defaults.
    desktop_only = TaskConfig(name="desktop", description="D.", modes=("desktop",))
    catalog = SYNTHETIC_TASKS + (desktop_only,)
    assert task_catalog.default_tasks("minimal", catalog) == ["a", "b", "c"]


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


def test_resolve_adds_transitive_dependencies() -> None:
    # Selecting c must pull in its transitive dependency a through b.
    assert task_catalog.resolve(["c"], SYNTHETIC_TASKS) == ["a", "b", "c"]


def test_resolve_puts_dependencies_before_the_task() -> None:
    assert task_catalog.resolve(["b"], SYNTHETIC_TASKS) == ["a", "b"]


def test_resolve_keeps_catalog_order_and_deduplicates() -> None:
    result = task_catalog.resolve(["c", "a", "c"], SYNTHETIC_TASKS)
    assert result == ["a", "b", "c"]


def test_resolve_ignores_unknown_names() -> None:
    # The engine validates selections before resolving; resolve stays lenient.
    assert task_catalog.resolve(["nope"], SYNTHETIC_TASKS) == []


def test_unknown_tasks_reports_unknown_names() -> None:
    assert task_catalog.unknown_tasks(["nope", "cli_tools"], TASKS) == ["nope"]


def test_by_name_matches_case_insensitively() -> None:
    assert task_catalog.by_name("CLI_TOOLS", TASKS) is not None
    assert task_catalog.by_name("Cli_Tools", TASKS) is not None
    assert task_catalog.by_name("nope", TASKS) is None


def test_unknown_tasks_matches_case_insensitively() -> None:
    # An unknown name is still reported, a known name in another case is not.
    assert task_catalog.unknown_tasks(["NOPE", "CLI_TOOLS"], TASKS) == ["NOPE"]


def test_resolve_matches_selection_case_insensitively() -> None:
    # Selection names are matched case-insensitively; the result carries the
    # canonical catalog names.
    assert task_catalog.resolve(["CLI_TOOLS"], TASKS) == [
        "add_extra_repos",
        "cli_tools",
    ]
    assert task_catalog.resolve(["C", "A"], SYNTHETIC_TASKS) == ["a", "b", "c"]


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
