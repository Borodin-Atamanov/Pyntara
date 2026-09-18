"""Guards of the task values.

The values package is the single place a value is declared, so these guards
keep the package honest: every declared value passes the rule of its own
annotation, the short list of extra rules holds, the names a module tells its
readers to read are exactly the names it declares, and every declared value is
read by some module of the package.

The rule of a value is derived from its annotation, so a new value needs no
line here. A rule that an annotation cannot express, and where a wrong value
would break the machine silently, is written once in EXTRA_VALUE_RULES; a
guard proves every name in that list is a declared value, so a typo cannot
hide there.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import get_type_hints

import pytest
from value_checks import (
    ValueRuleError,
    check_absolute_path,
    check_file_mode,
    check_nonempty_text,
    check_nonempty_text_tuple,
    check_not_negative_int,
    check_real_package_names,
    check_shipped_value,
    check_vault_entry_title,
)

import pyntara

# Every values module of the package, by its name inside pyntara.values.
VALUES_MODULE_NAMES: tuple[str, ...] = (
    "add_extra_repos",
    "chrome_setup",
    "cli_tools",
    "common",
    "dnsproxy_setup",
    "ffmpeg_setup",
    "hostname",
    "imagemagick_setup",
    "kde_keyboard_setup",
    "kde_settings",
    "local_vault_setup",
    "nextdns_setup_system_wide",
    "playwright_setup",
    "rustdesk_setup",
    "scrcpy_setup",
    "sotavpn_setup",
    "ssh_client_setup",
    "swapfile_service_install",
    "tasks",
    "telegram_setup",
    "vault_structure",
    "vocalinux_setup",
    "zram_service",
    "zswap_service",
)

# The name of the list a values module declares next to its values, which
# names the values its task reads.
READ_VALUE_NAMES_ATTRIBUTE = "READ_VALUE_NAMES"

# The rules an annotation cannot express, one line per value: its module, its
# name and the rule. A file mode is a whole number, so the generic pass sees
# only an int, while a mode of zero would leave the file unusable without a
# word; that is the kind of value that belongs here. A package list is a tuple
# of texts for the generic pass, while a virtual package name in it would make
# the task reinstall that package on every run without ever reaching its goal.
EXTRA_VALUE_RULES: tuple[tuple[str, str, Callable[[object, str], object]], ...] = (
    ("cli_tools", "PACKAGES", check_real_package_names),
    ("common", "EXECUTABLE_FILE_MODE", check_file_mode),
    ("common", "LAUNCHER_FILE_MODE", check_file_mode),
    ("ffmpeg_setup", "WAYRECORD_FILE_MODE", check_file_mode),
    ("local_vault_setup", "LOCAL_VAULT_FILE_MODE", check_file_mode),
    ("local_vault_setup", "PASS_DIR_MODE", check_file_mode),
    ("local_vault_setup", "PASS_FILE_MODE", check_file_mode),
    ("local_vault_setup", "PASS_FILE_WRITABLE_MODE", check_file_mode),
    ("local_vault_setup", "SECRETS_DIR_MODE", check_file_mode),
    ("local_vault_setup", "VAULT_PASSWORD_ENTRY_TITLE", check_vault_entry_title),
    ("common", "PROFILE_ID_FILE_MODE", check_file_mode),
    ("rustdesk_setup", "ID_FILE_MODE", check_file_mode),
    ("rustdesk_setup", "VAULT_ENTRY_TITLE", check_vault_entry_title),
    ("scrcpy_setup", "FALLBACK_PACKAGES", check_nonempty_text_tuple),
    ("sotavpn_setup", "KEY_ENTRY_TITLE", check_vault_entry_title),
    ("ssh_client_setup", "DROPIN_FILE_MODE", check_file_mode),
    ("swapfile_service_install", "SWAPFILE_MODE", check_file_mode),
    ("telegram_setup", "ICON_FILE_MODE", check_file_mode),
    ("zram_service", "HOT_ADD_READABLE_MODE_BIT", check_file_mode),
)

# Values the rule of their annotation refuses while the shipped value is
# legitimate: the one place a rule is overridden for a single value, and every
# entry is a decision with a reason written beside it (point 56 of the plan).
# A guard proves each entry names a declared value that the generic pass really
# refuses, so an exemption that has become unnecessary fails the suite.
EXEMPT_VALUES: tuple[tuple[str, str], ...] = (
    # The separator between the proquint words of the RustDesk password is one
    # space, so the text carries no letter on purpose.
    ("rustdesk_setup", "PASSWORD_SEPARATOR"),
)


def _source_root() -> Path:
    """The directory of the package sources the guards read."""

    package_file = pyntara.__file__
    assert package_file is not None
    return Path(package_file).resolve().parent


# Directories outside the package that read values too: the maintenance scripts
# of the repository, which import a values module and are not part of the wheel.
EXTRA_SOURCE_DIRECTORIES: tuple[str, ...] = ("secrets",)


def _scanned_source_paths() -> list[Path]:
    """Every source file whose reads of values count, package and scripts."""

    paths = list(_source_root().rglob("*.py"))
    repository_root = _source_root().parents[1]
    for directory_name in EXTRA_SOURCE_DIRECTORIES:
        directory = repository_root / directory_name
        if directory.is_dir():
            paths.extend(directory.rglob("*.py"))
    return sorted(path for path in paths if "__pycache__" not in path.parts)


def _values_module(module_name: str) -> object:
    """The values module of the package, imported by name."""

    return __import__(f"pyntara.values.{module_name}", fromlist=["*"])


def _declared_value_names(module_name: str) -> set[str]:
    """The names a values module declares by assignment, its list excluded.

    The names are read from the module source rather than from the module
    object, so a name the module imports, such as Path, is not mistaken for a
    value.
    """

    source = (_source_root() / "values" / f"{module_name}.py").read_text(
        encoding="utf-8"
    )
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    names.discard(READ_VALUE_NAMES_ATTRIBUTE)
    return names


def _read_names_by_attribute() -> dict[str, set[str]]:
    """Names read as attributes of a values module, per module.

    Every scanned source file is parsed: a module that imports a values module
    under an alias is followed, so a read through an alias counts and a read
    that never happens leaves the name unread. The scan covers the package and
    the maintenance scripts under secrets/, because a value read only there is
    not dead.
    """

    reads: dict[str, set[str]] = {name: set() for name in VALUES_MODULE_NAMES}
    for path in _scanned_source_paths():
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


def test_every_shipped_value_passes_the_rule_of_its_annotation() -> None:
    # The values that ship are the values the machine runs, and no task test
    # exercises them: a task test points the values at its fixture tree. This
    # pass is what looks at the shipped values, and it reads the annotation of
    # each of them instead of a list somebody must remember to extend.
    for module_name in VALUES_MODULE_NAMES:
        module = _values_module(module_name)
        for name, annotation in sorted(get_type_hints(module).items()):
            if name == READ_VALUE_NAMES_ATTRIBUTE:
                continue
            if (module_name, name) in EXEMPT_VALUES:
                continue
            check_shipped_value(
                getattr(module, name), annotation, f"{module_name}.{name}"
            )


def test_every_exempt_value_is_declared_and_otherwise_refused() -> None:
    # An exemption that names nothing would look like protection, and one whose
    # value the generic pass now accepts is a leftover: both fail here.
    for module_name, name in EXEMPT_VALUES:
        assert name in _declared_value_names(module_name), (
            f"exemption naming no declared value: {module_name}.{name}"
        )
        module = _values_module(module_name)
        annotation = get_type_hints(module)[name]
        with pytest.raises(ValueRuleError):
            check_shipped_value(
                getattr(module, name), annotation, f"{module_name}.{name}"
            )


def test_every_extra_rule_passes_on_its_shipped_value() -> None:
    for module_name, name, rule in EXTRA_VALUE_RULES:
        module = _values_module(module_name)
        assert rule(getattr(module, name), f"{module_name}.{name}") == getattr(
            module, name
        )


def test_every_extra_rule_names_a_declared_value() -> None:
    # A rule for a name that no module declares would never run and would look
    # like protection, so the suite refuses it.
    unknown: list[str] = []
    for module_name, name, _ in EXTRA_VALUE_RULES:
        if name not in _declared_value_names(module_name):
            unknown.append(f"{module_name}.{name}")
    assert not unknown, f"extra rules naming no declared value: {unknown}"


@pytest.mark.parametrize("value", ["", "   ", None, 42])
def test_the_generic_rule_refuses_a_text_with_nothing_in_it(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_shipped_value(value, str, "section.NAME")


@pytest.mark.parametrize("value", [-1, 1.5, "3", True, None])
def test_the_generic_rule_refuses_a_negative_whole_number(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_shipped_value(value, int, "section.NAME")


@pytest.mark.parametrize(
    "value", ["relative/path", "etc/hostname", "", None, 42, Path("etc/hostname")]
)
def test_the_generic_rule_refuses_a_path_that_is_not_absolute(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_shipped_value(value, Path, "section.NAME")


@pytest.mark.parametrize(
    "value", [(), [], "hostnamectl", ("hostnamectl", ""), ("hostnamectl", 1), None]
)
def test_the_generic_rule_refuses_a_tuple_that_is_no_command(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_shipped_value(value, tuple[str, ...], "section.NAME")


def test_the_generic_rule_leaves_an_annotation_it_does_not_read_alone() -> None:
    # A float, a bool or a record type is not judged by the generic pass: the
    # second layer takes such a value when a wrong one would be silent.
    check_shipped_value(-5.0, float, "section.NAME")
    check_shipped_value(False, bool, "section.NAME")


@pytest.mark.parametrize("value", [0, -1, 0o10000, 493.0, "0755", True, None])
def test_the_file_mode_rule_refuses_a_value_that_is_no_mode(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_file_mode(value, "section.NAME")


@pytest.mark.parametrize("value", [42, "", "   ", None, ("a",)])
def test_the_nonempty_text_rule_refuses_a_text_with_nothing_in_it(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_nonempty_text(value, "section.NAME")


@pytest.mark.parametrize("value", ["relative/path", "etc/hostname", "", None, 42])
def test_the_absolute_path_rule_refuses_a_path_that_is_not_absolute(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_absolute_path(value, "section.NAME")


@pytest.mark.parametrize("value", [-1, 4.0, "4", True, None])
def test_the_whole_number_rule_refuses_a_count_below_zero(value: object) -> None:
    with pytest.raises(ValueRuleError):
        check_not_negative_int(value, "section.NAME")


def test_the_whole_number_rule_allows_zero() -> None:
    # Zero is a value like any other: it means no attempt, no wait or no
    # permission to spare, and the tool that reads it reports itself.
    assert check_not_negative_int(0, "section.NAME") == 0


@pytest.mark.parametrize(
    "value",
    [(), [], "hostnamectl", ("hostnamectl", ""), ("hostnamectl", 1), None],
)
def test_the_text_tuple_rule_refuses_a_value_that_is_no_command(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_nonempty_text_tuple(value, "section.NAME")


def test_the_real_package_rule_refuses_a_virtual_package_name() -> None:
    # A virtual name in the list is invisible to dpkg-query, so the package
    # would be installed on every run and never reach the installed state.
    with pytest.raises(ValueRuleError):
        check_real_package_names(("mc", "exiftool"), "cli_tools.PACKAGES")
    with pytest.raises(ValueRuleError):
        check_real_package_names(("mc",), "cli_tools.PACKAGES")
    assert check_real_package_names(
        ("mc", "libimage-exiftool-perl"), "cli_tools.PACKAGES"
    ) == ("mc", "libimage-exiftool-perl")


def test_the_vault_entry_title_rule_refuses_a_title_the_structure_lacks() -> None:
    # The runtime vault carries the password under this title: a title no entry
    # of the structure holds would leave the vault password unreadable while
    # the task looked finished, which is the silent failure the rule refuses.
    with pytest.raises(ValueRuleError):
        check_vault_entry_title("no_such_entry", "local_vault_setup.TITLE")
    assert check_vault_entry_title(
        "pyntara_local_vault_password", "local_vault_setup.TITLE"
    ) == "pyntara_local_vault_password"


def test_the_read_list_names_exactly_the_declared_values() -> None:
    # The list a task reads is what the task reports when a value is missing,
    # so a value left out of the list would be read without ever being
    # reported, and a name in the list that nothing declares would turn into a
    # permanent warning.
    for module_name in VALUES_MODULE_NAMES:
        module = _values_module(module_name)
        listed = set(getattr(module, READ_VALUE_NAMES_ATTRIBUTE))
        assert listed == _declared_value_names(module_name), module_name


def test_every_declared_value_is_read_somewhere() -> None:
    # A value nobody reads is dead: it names a machine setting the run never
    # uses, and it would stay invisible until someone trusted it.
    reads = _read_names_by_attribute()
    unread: list[str] = []
    for module_name in VALUES_MODULE_NAMES:
        for name in sorted(_declared_value_names(module_name) - reads[module_name]):
            unread.append(f"{module_name}.{name}")
    assert not unread, f"values no module reads: {unread}"
