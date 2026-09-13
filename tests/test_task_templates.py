"""Every template the config names must exist in this clone.

A task renders its templates from files under task_data/<section>/ of the
clone the run started from (architecture contract, Configuration). The name
of such a file is a config value, and a wrong name compiles, passes every
unit test that renders a fixture of its own, and fails on the target
machine, where the shipped clone is the only clone. Two defects of that
kind happened while the values moved into the config: a clone root one
directory too deep, and template names written in the code. The rules here
cover the other direction, the names written in the config:

The value of a key whose name ends in _template_file_name is the name of a
file under task_data/<section>/ of the section that holds the key.

A string value that starts with task_data/ is a path from the clone root,
the shape a section uses when the template of a whole file tree is named
rather than a single file.

Both rules read config/ as TOML, section by section, and never import a
task module: a template nobody reads is out of scope here, while a template
named and missing is a failure.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
TASK_DATA_DIR = REPO_ROOT / "task_data"


def _string_values(table: dict[str, object], prefix: str = ""):
    """Yield the dotted key and the string of every string value."""

    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _string_values(value, f"{dotted}.")
        elif isinstance(value, str):
            yield dotted, value


def _sections() -> list[tuple[str, dict[str, object]]]:
    """Every config section with its parsed table, by file name."""

    sections: list[tuple[str, dict[str, object]]] = []
    for path in sorted(CONFIG_DIR.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        sections.append((path.stem, data))
    return sections


def test_every_named_template_exists_in_its_task_data_directory() -> None:
    missing: list[str] = []
    for section, data in _sections():
        for key, value in _string_values(data):
            if not key.endswith("_template_file_name"):
                continue
            candidate = TASK_DATA_DIR / section / value
            if not candidate.is_file():
                missing.append(f"{key} = {value!r}")
    assert not missing, (
        "templates named by the config and missing under "
        f"task_data/<section>/: {missing}"
    )


def test_every_task_data_path_exists_in_the_clone() -> None:
    missing: list[str] = []
    for section, data in _sections():
        for key, value in _string_values(data):
            if not value.startswith("task_data/"):
                continue
            if not (REPO_ROOT / value).is_file():
                missing.append(f"{key} = {value!r}")
    assert not missing, f"paths named by the config and missing: {missing}"


def test_every_task_data_directory_belongs_to_a_config_section() -> None:
    # The directory of a task is named after its catalog entry, which is the
    # name of its config section and of its module (task-model contract), so
    # a stray directory is either a renamed task or a leftover of one.
    sections = {section for section, _data in _sections()}
    orphans = sorted(
        directory.name
        for directory in TASK_DATA_DIR.iterdir()
        if directory.is_dir() and directory.name not in sections
    )
    assert not orphans, f"task_data directories without a config section: {orphans}"
