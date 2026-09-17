"""Guards of the task values.

The values package is the single place a value is declared, so these
guards keep the package honest: the shipped values pass every rule of
tests/value_checks.py, the names a module tells its readers to read are
exactly the names it declares, and every declared name is read by some
module of the package. A value that no module reads is dead, and a name a
module reads without it being declared is the typo the runtime answers
with a warning instead of an exception, which is why the suite refuses it
before it reaches a machine.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from value_checks import (
    ValueRuleError,
    check_hostname_file,
    check_random_bytes,
    check_set_hostname_command,
)

import pyntara
from pyntara.values import hostname as hostname_values

# Every values module of the package, by its name inside pyntara.values.
VALUES_MODULE_NAMES: tuple[str, ...] = ("hostname",)

# The name of the list a values module declares next to its values, which
# names the values its task reads.
READ_VALUE_NAMES_ATTRIBUTE = "READ_VALUE_NAMES"


def _source_root() -> Path:
    """The directory of the package sources the guards read."""

    package_file = pyntara.__file__
    assert package_file is not None
    return Path(package_file).resolve().parent


def _declared_value_names(module: object) -> set[str]:
    """The uppercase names a values module declares, its list excluded."""

    return {
        name
        for name in dir(module)
        if name[:1].isupper() and name != READ_VALUE_NAMES_ATTRIBUTE
    }


def _read_names_by_attribute() -> dict[str, set[str]]:
    """Names read as attributes of a values module, per module.

    Every module of the package is parsed: a module that imports a values
    module under an alias is followed, so a read through an alias counts
    and a read that never happens leaves the name unread.
    """

    reads: dict[str, set[str]] = {name: set() for name in VALUES_MODULE_NAMES}
    for path in sorted(_source_root().rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "pyntara.values"
            ):
                for imported in node.names:
                    if imported.name in VALUES_MODULE_NAMES:
                        aliases[imported.asname or imported.name] = imported.name
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
            ):
                reads[aliases[node.value.id]].add(node.attr)
    return reads


def test_shipped_hostname_values_pass_every_rule() -> None:
    # The values that ship are the values the machine runs, so the rules
    # are applied to them and not only to the values a test makes up.
    assert check_hostname_file(hostname_values.HOSTNAME_FILE) == (
        hostname_values.HOSTNAME_FILE
    )
    assert check_random_bytes(hostname_values.RANDOM_BYTES) == (
        hostname_values.RANDOM_BYTES
    )
    assert check_set_hostname_command(hostname_values.SET_HOSTNAME_COMMAND) == (
        hostname_values.SET_HOSTNAME_COMMAND
    )


@pytest.mark.parametrize("value", [42, "", "   ", None, ("a",)])
def test_the_hostname_file_rule_refuses_a_value_that_is_no_path(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_hostname_file(value)


@pytest.mark.parametrize("value", [0, -1, 4.0, "4", True, None])
def test_the_random_bytes_rule_refuses_a_value_that_is_no_count(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_random_bytes(value)


@pytest.mark.parametrize(
    "value",
    [(), [], "hostnamectl", ("hostnamectl", ""), ("hostnamectl", 1), None],
)
def test_the_command_rule_refuses_a_value_that_is_no_command(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_set_hostname_command(value)


def test_the_read_list_names_exactly_the_declared_values() -> None:
    # The list a task reads is what the task reports when a value is
    # missing, so a value left out of the list would be read without ever
    # being reported, and a name in the list that nothing declares would
    # turn into a permanent warning.
    for module_name in VALUES_MODULE_NAMES:
        module = __import__(f"pyntara.values.{module_name}", fromlist=["*"])
        listed = set(getattr(module, READ_VALUE_NAMES_ATTRIBUTE))
        assert listed == _declared_value_names(module), module_name


def test_every_declared_value_is_read_somewhere() -> None:
    # A value nobody reads is dead: it names a machine setting the run
    # never uses, and it would stay invisible until someone trusted it.
    reads = _read_names_by_attribute()
    unread: list[str] = []
    for module_name in VALUES_MODULE_NAMES:
        module = __import__(f"pyntara.values.{module_name}", fromlist=["*"])
        for name in sorted(_declared_value_names(module) - reads[module_name]):
            unread.append(f"{module_name}.{name}")
    assert not unread, f"values no module reads: {unread}"
