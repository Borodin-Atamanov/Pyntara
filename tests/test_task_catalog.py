"""Unit tests for the task catalog logic.

The catalog data lives in pyntara.values.tasks; this module tests the logic
that operates on it. The mechanics of dependency resolution are tested on a
small synthetic catalog so they never depend on specific task names. Data
checks against the real catalog only reference implemented tasks: future
tasks are expected to change and must not be mentioned by name in tests.
inst.sh never parses the catalog: the engine owns defaults, validation and
dependency resolution.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pyntara import task_catalog
from pyntara.values.tasks import CATALOG, MODES, TaskSpec

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS = CATALOG


# A synthetic three-task chain with one transitive dependency. Mechanics
# tests use it so they stay valid regardless of which tasks exist or are
# implemented; only the two implemented tasks are referenced by name in
# data tests. The modes of the fixture are local to it and say nothing about
# the shipped vocabulary.
_SYNTHETIC_MODES = ("minimal", "server", "desktop")
SYNTHETIC_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(name="a", description="A.", modes=_SYNTHETIC_MODES),
    TaskSpec(name="b", description="B.", depends=("a",), modes=_SYNTHETIC_MODES),
    TaskSpec(name="c", description="C.", depends=("b",), modes=_SYNTHETIC_MODES),
)


def test_the_vocabulary_names_the_installed_modes() -> None:
    # The vocabulary is open, so the test asks for the names a machine is
    # installed with instead of pinning the whole tuple: a mode added later is
    # a new line in the catalog and never a failure here.
    for name in ("minimal", "server", "desktop", "fast_desktop"):
        assert name in MODES


def test_the_vocabulary_holds_no_duplicate_name() -> None:
    assert len(MODES) == len(set(MODES))


def test_default_tasks_match_mode_membership_exactly() -> None:
    for mode in MODES:
        expected = [task.name for task in TASKS if mode in task.modes]
        assert task_catalog.default_tasks(mode, TASKS) == expected


def test_default_tasks_use_configured_mode_membership() -> None:
    # A task listed only for desktop must never appear in minimal defaults.
    desktop_only = TaskSpec(name="desktop", description="D.", modes=("desktop",))
    catalog = SYNTHETIC_TASKS + (desktop_only,)
    assert task_catalog.default_tasks("minimal", catalog) == ["a", "b", "c"]


def test_add_extra_repos_leads_the_installed_modes() -> None:
    # add_extra_repos must run before any package install, so it leads the
    # default task set of every mode a machine is installed with. The modes
    # are named one by one, so a mode added later is not a failure here.
    for mode in ("minimal", "server", "desktop", "fast_desktop"):
        defaults = task_catalog.default_tasks(mode, TASKS)
        assert defaults[0] == "add_extra_repos"


def test_resolve_cli_tools_lite_setup_pulls_add_extra_repos() -> None:
    # Selecting cli_tools_lite_setup alone must enable add_extra_repos first, because
    # its packages live in universe and multiverse.
    assert task_catalog.resolve(["cli_tools_lite_setup"], TASKS) == [
        "add_extra_repos",
        "cli_tools_lite_setup",
    ]


def test_resolve_dnsproxy_pulls_the_nextdns_profile_task() -> None:
    # dnsproxy reads the NextDNS profile from the file that
    # nextdns_setup_system_wide writes, so selecting dnsproxy alone must
    # enable that task first.
    result = task_catalog.resolve(["dnsproxy_setup"], TASKS)
    assert "nextdns_setup_system_wide" in result
    assert result.index("nextdns_setup_system_wide") < result.index("dnsproxy_setup")


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
    assert task_catalog.unknown_tasks(["nope", "cli_tools_lite_setup"], TASKS) == ["nope"]


def test_by_name_matches_case_insensitively() -> None:
    assert task_catalog.by_name("CLI_TOOLS_LITE_SETUP", TASKS) is not None
    assert task_catalog.by_name("Cli_Tools_Lite_Setup", TASKS) is not None
    assert task_catalog.by_name("nope", TASKS) is None


def test_unknown_tasks_matches_case_insensitively() -> None:
    # An unknown name is still reported, a known name in another case is not.
    assert task_catalog.unknown_tasks(["NOPE", "CLI_TOOLS_LITE_SETUP"], TASKS) == ["NOPE"]


def test_resolve_matches_selection_case_insensitively() -> None:
    # Selection names are matched case-insensitively; the result carries the
    # canonical catalog names.
    assert task_catalog.resolve(["CLI_TOOLS_LITE_SETUP"], TASKS) == [
        "add_extra_repos",
        "cli_tools_lite_setup",
    ]
    assert task_catalog.resolve(["C", "A"], SYNTHETIC_TASKS) == ["a", "b", "c"]


def test_validate_mode_accepts_known_modes() -> None:
    for mode in MODES:
        task_catalog.validate_mode(mode)


def test_validate_mode_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown install mode"):
        task_catalog.validate_mode("fancy")


def test_a_written_mode_name_is_read_without_case_and_separator() -> None:
    # A mode is written by hand, so the letter case and the choice between a
    # hyphen and an underscore name the same mode as the declared spelling.
    for written in ("fast_desktop", "fast-desktop", "Fast-Desktop", "FAST_DESKTOP"):
        assert task_catalog.canonical_mode_name(written) == "fast_desktop"


def test_a_written_mode_name_that_names_no_mode_has_no_answer() -> None:
    for written in ("fancy", "fast desktop", "", "fast_desktops"):
        assert task_catalog.canonical_mode_name(written) is None


def test_validate_mode_reads_a_written_spelling_as_the_run_does() -> None:
    # The guard and the run read a written name the same way: a guard that
    # accepted fewer spellings than the run would refuse a mode the run applies.
    for written in ("fast-desktop", "Fast_Desktop", "SERVER"):
        task_catalog.validate_mode(written)


def test_catalog_names_are_unique() -> None:
    names = [task.name for task in TASKS]
    assert len(names) == len(set(names))


def test_dependencies_refer_to_known_tasks() -> None:
    known = {task.name for task in TASKS}
    for task in TASKS:
        for dep in task.depends:
            assert dep in known


def test_task_spec_is_frozen() -> None:
    task = TaskSpec(name="x", description="X")
    with pytest.raises(AttributeError):
        task.name = "y"  # type: ignore[misc]


def test_task_modules_do_not_write_their_own_name() -> None:
    # The catalog is the single source of truth for task names and the
    # runner hands each task its own name (docs/contracts/task-model.md),
    # so a module never writes it: it reads the name from the context for
    # the force check and for the task-data directory. A literal there is
    # a copy of a declared value, which the config spec forbids.
    force_literal = re.compile(r'"[a-z0-9_]+" (?:not )?in ctx\.force_tasks')
    data_dir_literal = re.compile(r'task_data_dir\([^)]*"', re.DOTALL)
    src_root = REPO_ROOT / "src" / "pyntara"
    offenders: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if force_literal.search(text) or data_dir_literal.search(text):
            offenders.append(str(path.relative_to(src_root)))
    assert not offenders, f"modules writing a task name: {offenders}"
