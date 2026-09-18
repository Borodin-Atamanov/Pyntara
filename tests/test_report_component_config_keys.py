"""The key list of each report component names exactly the keys it reads.

A list of the key names a deployed component reads lives in the config
layer next to the fields it names, and the component imports it
(docs/spec/config-content.md, Where a value is declared and checked).
tests/test_config_coverage.py proves that every name of such a list is a
key of its table and that the component reads the list of the config
layer instead of a copy. It cannot prove the other direction for the
components it covers, because they read their keys through shared
modules, and the test says so.

The report commands of the System Metrics collector that still read
the config layer (the public address report and the country report) read
the keys of their own section directly in one module, so for them the
other direction is provable: this check parses the module, follows the
local alias of the section (setup = cfg.<section>) and refuses a key
that is read without being named in the list, as well as a name of the
list that nothing reads. A key the code starts to read therefore reaches
the one-line report of an incomplete config in the same commit. The
i2pd, tor and yggdrasil address commands read declared values now and
need no such list.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType

import pytest

from pyntara import country_report, public_address_report
from pyntara.config import three_x_ui_xray_setup

CASES: tuple[tuple[ModuleType, str, ModuleType, str], ...] = (
    (
        country_report,
        "three_x_ui_xray_setup",
        three_x_ui_xray_setup,
        "COUNTRY_REPORT_CONFIG_KEYS",
    ),
    (
        public_address_report,
        "three_x_ui_xray_setup",
        three_x_ui_xray_setup,
        "PUBLIC_ADDRESS_CONFIG_KEYS",
    ),
)


def _aliases_of_section(tree: ast.AST, section: str) -> set[str]:
    """Local names bound to that config section, such as setup."""

    aliases: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "cfg"
            and node.value.attr == section
            and isinstance(node.targets[0], ast.Name)
        ):
            aliases.add(node.targets[0].id)
    return aliases


def _keys_read(module: ModuleType, section: str) -> set[str]:
    """Every attribute of that config section the module reads."""

    source = Path(module.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    aliases = _aliases_of_section(tree, section)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        through_alias = isinstance(base, ast.Name) and base.id in aliases
        on_the_config = (
            isinstance(base, ast.Attribute)
            and isinstance(base.value, ast.Name)
            and base.value.id == "cfg"
            and base.attr == section
        )
        if through_alias or on_the_config:
            names.add(node.attr)
    return names


@pytest.mark.parametrize(
    "module, section, section_module, list_name",
    CASES,
    ids=[case[0].__name__ for case in CASES],
)
def test_list_names_every_key_the_component_reads(
    module: ModuleType,
    section: str,
    section_module: ModuleType,
    list_name: str,
) -> None:
    # The list and the code are compared in both directions, so the
    # one-line report of a missing value can never fall behind the reads.
    names = getattr(section_module, list_name)
    declared = set(names)
    read = _keys_read(module, section)
    undeclared = sorted(read - declared)
    unread = sorted(declared - read)
    assert undeclared == [], (
        f"{module.__name__} reads {undeclared} of the [{section}] table "
        f"without naming them in {list_name}"
    )
    assert unread == [], (
        f"{module.__name__} names {unread} in {list_name} without reading them"
    )
    assert len(names) == len(declared), (
        f"{list_name} of {section_module.__name__} names a key twice"
    )


@pytest.mark.parametrize(
    "module, section, section_module, list_name",
    CASES,
    ids=[case[0].__name__ for case in CASES],
)
def test_component_reports_the_missing_keys_through_the_list(
    module: ModuleType,
    section: str,
    section_module: ModuleType,
    list_name: str,
) -> None:
    # The report of an incomplete config is built from the list of the
    # config layer and not from a tuple written in the component.
    source = Path(module.__file__ or "").read_text(encoding="utf-8")
    aliases = _aliases_of_section(ast.parse(source), section)
    assert any(
        f"absent_config_keys({alias}, {list_name})" in source for alias in aliases
    ), (
        f"{module.__name__} reports the absent keys of [{section}] with "
        f"something other than {list_name}"
    )
