"""Config tests for the [[tasks]] catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import assert_config_error, base_config, write_config

from pyntara.config import load_config


@pytest.mark.parametrize(
    "content",
    [
        # task name is a number, not a string
        base_config().replace('name = "users"', "name = 1"),
        # task name is an empty string
        base_config().replace('name = "users"', 'name = ""'),
        # task name contains a space, not an identifier
        base_config().replace('name = "users"', 'name = "my task"'),
        # task description is a number, not a string
        base_config().replace('description = "Create users."', "description = 1"),
        # task depends is a string, not an array
        base_config().replace("depends = []", 'depends = "users"'),
        # task depends contains a number, not strings
        base_config().replace("depends = []", "depends = [1]"),
        # task depends names a task that is not listed earlier
        base_config().replace("depends = []", 'depends = ["later"]'),
        # task modes is a string, not an array
        base_config().replace('modes = ["minimal"]', 'modes = "minimal"'),
        # task modes contains an unknown install mode
        base_config().replace('modes = ["minimal"]', 'modes = ["fancy"]'),
        # task modes contains a duplicate
        base_config().replace(
            'modes = ["minimal"]', 'modes = ["minimal", "minimal"]'
        ),
        # task modes contains a number, not strings
        base_config().replace('modes = ["minimal"]', "modes = [1]"),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_missing_tasks_section_raises(tmp_path: Path) -> None:
    # The catalog is mandatory: without it the engine cannot compute the
    # task set (architecture contract section 3). The base config is used
    # with its [[tasks]] section cut off.
    assert_config_error(
        tmp_path,
        base_config().split("[[tasks]]")[0],
        match="\\[tasks\\]",
    )


def test_load_config_empty_tasks_raises(tmp_path: Path) -> None:
    # An empty catalog is invalid: nothing would be provisionable. The
    # tasks key must sit before any table header, or TOML would attach it
    # to the preceding table.
    assert_config_error(
        tmp_path,
        "tasks = []\n" + base_config().split("[[tasks]]")[0],
        match="at least one task",
    )


def test_load_config_rejects_duplicate_task_names(tmp_path: Path) -> None:
    assert_config_error(
        tmp_path,
        base_config() + '[[tasks]]\nname = "users"\ndescription = "Again."\n'
        "depends = []\nmodes = [\"minimal\"]\n",
        match="duplicate task name",
    )


def test_load_config_accepts_empty_modes(tmp_path: Path) -> None:
    # An empty modes list keeps the task in the catalog but in no install
    # mode, so it never runs in a default task set and only runs when
    # selected explicitly.
    config = load_config(
        write_config(
            tmp_path,
            base_config().replace('modes = ["minimal"]', "modes = []"),
        )
    )
    task = next(t for t in config.tasks if t.name == "users")
    assert task.modes == ()
