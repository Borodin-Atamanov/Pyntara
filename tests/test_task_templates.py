"""Every template the values name must exist in this clone.

A task renders its templates from files under task_data/<section>/ of the
clone the run started from (architecture contract, Configuration). The name
of such a file is a declared value, and a wrong name compiles, passes every
unit test that renders a fixture of its own, and fails on the target
machine, where the shipped clone is the only clone. Two defects of that
kind happened while the values moved into the package: a clone root one
directory too deep, and template names written in the code. The rules here
cover the other direction, the names declared in the values modules:

The value of a name ending in _TEMPLATE_FILE_NAME is a file under
task_data/<section>/ of the module that declares it.

The value of a name ending in _SCRIPT_FILE_NAME is a client the task runs,
a file under task_data/<section>/ of the same module, because a body longer
than five lines belongs in a file and not in the code.

The value of a name ending in PYTHON_SCRIPT_COMMAND runs such a client, so it
names the rendered client as {client_file} and never the -c flag of the
interpreter: the program text of a client stays in its file, and one call of
it keeps one line in the log of a run.

A string value that starts with task_data/ is a path from the clone root,
the shape a module uses when the template of a whole file tree is named
rather than a single file.

The reader parses the values modules instead of importing them, so a module
that cannot be imported still has its names checked; the import rules of a
values module belong to tests/test_values.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

from pyntara.values import tasks as tasks_values

REPO_ROOT = Path(__file__).resolve().parents[1]
VALUES_DIR = REPO_ROOT / "src" / "pyntara" / "values"
TASK_DATA_DIR = REPO_ROOT / "task_data"


def _declared_nodes() -> list[tuple[str, str, ast.expr]]:
    """The module, the value name and the node of every declared value."""

    declared: list[tuple[str, str, ast.expr]] = []
    for path in sorted(VALUES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            name = ""
            value_node: ast.expr | None = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name, value_node = node.target.id, node.value
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                name, value_node = node.targets[0].id, node.value
            if name and value_node is not None:
                declared.append((path.stem, name, value_node))
    return declared


def _declared_strings() -> list[tuple[str, str, str]]:
    """The module, the value name and the string of every declared string."""

    return [
        (module, name, value_node.value)
        for module, name, value_node in _declared_nodes()
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str)
    ]


def _declared_command_parts() -> list[tuple[str, str, tuple[str, ...]]]:
    """The module, the value name and the parts of every declared command."""

    declared: list[tuple[str, str, tuple[str, ...]]] = []
    for module, name, value_node in _declared_nodes():
        if not isinstance(value_node, ast.Tuple):
            continue
        parts = [
            element.value
            for element in value_node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if len(parts) == len(value_node.elts):
            declared.append((module, name, tuple(parts)))
    return declared


def test_every_declared_client_command_runs_a_file_and_not_a_program() -> None:
    offenders: list[str] = []
    for module, name, parts in _declared_command_parts():
        if not name.endswith("PYTHON_SCRIPT_COMMAND"):
            continue
        if "-c" in parts or "{client_file}" not in parts:
            offenders.append(f"{module}.{name} = {parts!r}")
    assert not offenders, (
        "client commands that would run the text of a program: " f"{offenders}"
    )


def test_every_declared_template_exists_in_its_task_data_directory() -> None:
    missing: list[str] = []
    for module, name, value in _declared_strings():
        if not name.endswith("_TEMPLATE_FILE_NAME"):
            continue
        if not (TASK_DATA_DIR / module / value).is_file():
            missing.append(f"{module}.{name} = {value!r}")
    assert not missing, (
        "templates declared by the values and missing under "
        f"task_data/<section>/: {missing}"
    )


def test_every_declared_script_exists_in_its_task_data_directory() -> None:
    missing: list[str] = []
    for module, name, value in _declared_strings():
        if not name.endswith("_SCRIPT_FILE_NAME"):
            continue
        if not (TASK_DATA_DIR / module / value).is_file():
            missing.append(f"{module}.{name} = {value!r}")
    assert not missing, (
        "clients declared by the values and missing under "
        f"task_data/<section>/: {missing}"
    )


def test_every_declared_task_data_path_exists_in_the_clone() -> None:
    missing: list[str] = []
    for module, name, value in _declared_strings():
        if not value.startswith("task_data/"):
            continue
        if not (REPO_ROOT / value).is_file():
            missing.append(f"{module}.{name} = {value!r}")
    assert not missing, f"paths declared by the values and missing: {missing}"


def test_every_task_data_directory_belongs_to_a_task() -> None:
    # The directory of a task is named after its catalog entry, which is the
    # name of its module and of its values module (task-model contract), so a
    # stray directory is either a renamed task or a leftover of one.
    names = {spec.name for spec in tasks_values.CATALOG}
    orphans = sorted(
        directory.name
        for directory in TASK_DATA_DIR.iterdir()
        if directory.is_dir() and directory.name not in names
    )
    assert not orphans, f"task_data directories without a task: {orphans}"
