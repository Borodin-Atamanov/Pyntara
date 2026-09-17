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
    check_absolute_path,
    check_file_mode,
    check_nonempty_text,
    check_nonnegative_int,
    check_positive_int,
    check_text_tuple,
)

import pyntara
from pyntara.values import ffmpeg_setup as ffmpeg_values
from pyntara.values import hostname as hostname_values
from pyntara.values import imagemagick_setup as imagemagick_values
from pyntara.values import playwright_setup as playwright_values

# Every values module of the package, by its name inside pyntara.values.
VALUES_MODULE_NAMES: tuple[str, ...] = (
    "ffmpeg_setup",
    "hostname",
    "imagemagick_setup",
    "playwright_setup",
)

# The name of the list a values module declares next to its values, which
# names the values its task reads.
READ_VALUE_NAMES_ATTRIBUTE = "READ_VALUE_NAMES"


def _source_root() -> Path:
    """The directory of the package sources the guards read."""

    package_file = pyntara.__file__
    assert package_file is not None
    return Path(package_file).resolve().parent


def _declared_value_names(module_name: str) -> set[str]:
    """The names a values module declares by assignment, its list excluded.

    The names are read from the module source rather than from the module
    object, so a name the module imports, such as Path, is not mistaken for
    a value.
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
    assert (
        check_absolute_path(
            hostname_values.HOSTNAME_FILE, "hostname.HOSTNAME_FILE"
        )
        == hostname_values.HOSTNAME_FILE
    )
    assert (
        check_positive_int(hostname_values.RANDOM_BYTES, "hostname.RANDOM_BYTES")
        == hostname_values.RANDOM_BYTES
    )
    assert (
        check_text_tuple(
            hostname_values.SET_HOSTNAME_COMMAND,
            "hostname.SET_HOSTNAME_COMMAND",
        )
        == hostname_values.SET_HOSTNAME_COMMAND
    )


def test_shipped_imagemagick_values_pass_every_rule() -> None:
    assert (
        check_text_tuple(imagemagick_values.PACKAGES, "imagemagick_setup.PACKAGES")
        == imagemagick_values.PACKAGES
    )
    assert (
        check_absolute_path(
            str(imagemagick_values.POLICY_PATH),
            "imagemagick_setup.POLICY_PATH",
        )
        == str(imagemagick_values.POLICY_PATH)
    )
    assert (
        check_nonempty_text(
            imagemagick_values.POLICY_TEMPLATE_FILE_NAME,
            "imagemagick_setup.POLICY_TEMPLATE_FILE_NAME",
        )
        == imagemagick_values.POLICY_TEMPLATE_FILE_NAME
    )
    assert (
        check_nonempty_text(
            imagemagick_values.POLICY_BACKUP_FILE_SUFFIX,
            "imagemagick_setup.POLICY_BACKUP_FILE_SUFFIX",
        )
        == imagemagick_values.POLICY_BACKUP_FILE_SUFFIX
    )
    assert (
        check_positive_int(
            imagemagick_values.PACKAGE_STATUS_TIMEOUT_SECONDS,
            "imagemagick_setup.PACKAGE_STATUS_TIMEOUT_SECONDS",
        )
        == imagemagick_values.PACKAGE_STATUS_TIMEOUT_SECONDS
    )
    assert (
        check_nonnegative_int(
            imagemagick_values.PACKAGE_INSTALL_RETRIES,
            "imagemagick_setup.PACKAGE_INSTALL_RETRIES",
        )
        == imagemagick_values.PACKAGE_INSTALL_RETRIES
    )


def test_shipped_ffmpeg_values_pass_every_rule() -> None:
    assert (
        check_text_tuple(ffmpeg_values.PACKAGES, "ffmpeg_setup.PACKAGES")
        == ffmpeg_values.PACKAGES
    )
    assert (
        check_absolute_path(
            str(ffmpeg_values.WAYRECORD_BIN_PATH),
            "ffmpeg_setup.WAYRECORD_BIN_PATH",
        )
        == str(ffmpeg_values.WAYRECORD_BIN_PATH)
    )
    assert (
        check_absolute_path(
            str(ffmpeg_values.WAYRECORD_DESKTOP_PATH),
            "ffmpeg_setup.WAYRECORD_DESKTOP_PATH",
        )
        == str(ffmpeg_values.WAYRECORD_DESKTOP_PATH)
    )
    assert (
        check_file_mode(
            ffmpeg_values.WAYRECORD_FILE_MODE,
            "ffmpeg_setup.WAYRECORD_FILE_MODE",
        )
        == ffmpeg_values.WAYRECORD_FILE_MODE
    )
    assert (
        check_text_tuple(
            ffmpeg_values.WAYRECORD_SOURCE_FILE_NAMES,
            "ffmpeg_setup.WAYRECORD_SOURCE_FILE_NAMES",
        )
        == ffmpeg_values.WAYRECORD_SOURCE_FILE_NAMES
    )
    assert (
        check_nonempty_text(
            ffmpeg_values.WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME,
            "ffmpeg_setup.WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME",
        )
        == ffmpeg_values.WAYRECORD_DESKTOP_TEMPLATE_FILE_NAME
    )
    assert (
        check_nonempty_text(
            ffmpeg_values.WAYRECORD_BUILD_FILE_SUFFIX,
            "ffmpeg_setup.WAYRECORD_BUILD_FILE_SUFFIX",
        )
        == ffmpeg_values.WAYRECORD_BUILD_FILE_SUFFIX
    )
    assert (
        check_text_tuple(
            ffmpeg_values.WAYRECORD_BUILD_FLAGS_COMMAND,
            "ffmpeg_setup.WAYRECORD_BUILD_FLAGS_COMMAND",
        )
        == ffmpeg_values.WAYRECORD_BUILD_FLAGS_COMMAND
    )
    assert (
        check_text_tuple(
            ffmpeg_values.WAYRECORD_COMPILE_COMMAND,
            "ffmpeg_setup.WAYRECORD_COMPILE_COMMAND",
        )
        == ffmpeg_values.WAYRECORD_COMPILE_COMMAND
    )
    assert (
        check_positive_int(
            ffmpeg_values.PACKAGE_STATUS_TIMEOUT_SECONDS,
            "ffmpeg_setup.PACKAGE_STATUS_TIMEOUT_SECONDS",
        )
        == ffmpeg_values.PACKAGE_STATUS_TIMEOUT_SECONDS
    )
    assert (
        check_nonnegative_int(
            ffmpeg_values.PACKAGE_INSTALL_RETRIES,
            "ffmpeg_setup.PACKAGE_INSTALL_RETRIES",
        )
        == ffmpeg_values.PACKAGE_INSTALL_RETRIES
    )


def test_shipped_playwright_values_pass_every_rule() -> None:
    assert (
        check_nonempty_text(playwright_values.USERNAME, "playwright_setup.USERNAME")
        == playwright_values.USERNAME
    )
    assert (
        check_absolute_path(
            playwright_values.HOME_DIR, "playwright_setup.HOME_DIR"
        )
        == playwright_values.HOME_DIR
    )
    assert (
        check_text_tuple(playwright_values.PACKAGES, "playwright_setup.PACKAGES")
        == playwright_values.PACKAGES
    )
    assert (
        check_positive_int(
            playwright_values.PACKAGE_STATUS_TIMEOUT_SECONDS,
            "playwright_setup.PACKAGE_STATUS_TIMEOUT_SECONDS",
        )
        == playwright_values.PACKAGE_STATUS_TIMEOUT_SECONDS
    )
    assert (
        check_nonnegative_int(
            playwright_values.PACKAGE_INSTALL_RETRIES,
            "playwright_setup.PACKAGE_INSTALL_RETRIES",
        )
        == playwright_values.PACKAGE_INSTALL_RETRIES
    )
    assert (
        check_nonempty_text(
            playwright_values.CLI_PACKAGE, "playwright_setup.CLI_PACKAGE"
        )
        == playwright_values.CLI_PACKAGE
    )
    assert (
        check_nonempty_text(
            playwright_values.USER_PREFIX_RELATIVE_PATH,
            "playwright_setup.USER_PREFIX_RELATIVE_PATH",
        )
        == playwright_values.USER_PREFIX_RELATIVE_PATH
    )
    assert (
        check_nonempty_text(
            playwright_values.CLI_BIN_RELATIVE_PATH,
            "playwright_setup.CLI_BIN_RELATIVE_PATH",
        )
        == playwright_values.CLI_BIN_RELATIVE_PATH
    )
    assert (
        check_text_tuple(
            playwright_values.RUNUSER_COMMAND,
            "playwright_setup.RUNUSER_COMMAND",
        )
        == playwright_values.RUNUSER_COMMAND
    )
    assert (
        check_text_tuple(
            playwright_values.CLI_VERSION_COMMAND,
            "playwright_setup.CLI_VERSION_COMMAND",
        )
        == playwright_values.CLI_VERSION_COMMAND
    )
    assert (
        check_text_tuple(
            playwright_values.NPM_INSTALL_COMMAND,
            "playwright_setup.NPM_INSTALL_COMMAND",
        )
        == playwright_values.NPM_INSTALL_COMMAND
    )
    assert (
        check_positive_int(
            playwright_values.NPM_INSTALL_TIMEOUT_SECONDS,
            "playwright_setup.NPM_INSTALL_TIMEOUT_SECONDS",
        )
        == playwright_values.NPM_INSTALL_TIMEOUT_SECONDS
    )


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


@pytest.mark.parametrize("value", [0, -1, 4.0, "4", True, None])
def test_the_positive_int_rule_refuses_a_count_that_is_not_positive(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_positive_int(value, "section.NAME")


@pytest.mark.parametrize("value", [-1, 1.5, "3", True, None])
def test_the_nonnegative_int_rule_refuses_a_count_below_zero(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_nonnegative_int(value, "section.NAME")


@pytest.mark.parametrize("value", [0, -1, 0o10000, 493.0, "0755", True, None])
def test_the_file_mode_rule_refuses_a_value_that_is_no_mode(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_file_mode(value, "section.NAME")


@pytest.mark.parametrize(
    "value",
    [(), [], "hostnamectl", ("hostnamectl", ""), ("hostnamectl", 1), None],
)
def test_the_text_tuple_rule_refuses_a_value_that_is_no_command(
    value: object,
) -> None:
    with pytest.raises(ValueRuleError):
        check_text_tuple(value, "section.NAME")


def test_the_read_list_names_exactly_the_declared_values() -> None:
    # The list a task reads is what the task reports when a value is
    # missing, so a value left out of the list would be read without ever
    # being reported, and a name in the list that nothing declares would
    # turn into a permanent warning.
    for module_name in VALUES_MODULE_NAMES:
        module = __import__(f"pyntara.values.{module_name}", fromlist=["*"])
        listed = set(getattr(module, READ_VALUE_NAMES_ATTRIBUTE))
        assert listed == _declared_value_names(module_name), module_name


def test_every_declared_value_is_read_somewhere() -> None:
    # A value nobody reads is dead: it names a machine setting the run
    # never uses, and it would stay invisible until someone trusted it.
    reads = _read_names_by_attribute()
    unread: list[str] = []
    for module_name in VALUES_MODULE_NAMES:
        for name in sorted(_declared_value_names(module_name) - reads[module_name]):
            unread.append(f"{module_name}.{name}")
    assert not unread, f"values no module reads: {unread}"
