"""Rules of the task values, test side only.

A value is a typed constant of pyntara.values, so mypy checks its type before
any run. What is left is the shape a type cannot say, and it is checked in two
layers. The first layer is generic: the rule of a value follows from its own
annotation, so a new value is checked without anyone writing a rule for it,
which is what check_shipped_value does. The second layer is the short list of
rules a wrong value would break silently, or where two values must agree; the
list lives in tests/test_values.py as EXTRA_VALUE_RULES, one line per value.

Every rule takes the value and the dotted name it is reported under, returns
the value unchanged when the rule holds and raises ValueRuleError when it does
not, so a test feeds a rule a bad value without touching the shipped ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args, get_origin


class ValueRuleError(RuntimeError):
    """Raised when a shipped value breaks a rule that its type cannot say."""


def check_nonempty_text(value: object, name: str) -> str:
    """A text with something in it.

    An empty text is the shape a value takes when it is declared and never
    filled in: a path that names nothing, a file name that matches no file, a
    suffix that names no suffix.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueRuleError(f"{name} must be a non-empty text")
    return value


def check_not_negative_int(value: object, name: str) -> int:
    """A whole number of zero or more.

    A boolean is refused although Python counts it as a whole number, because
    it is not a count. Zero is allowed: it means no attempt, no wait or no
    permission to spare, which the tool that reads it reports itself.
    """

    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueRuleError(f"{name} must be a whole number of zero or more")
    return value


def check_absolute_path(value: object, name: str) -> str:
    """A text or a Path that names an absolute path.

    A relative path is resolved against the working directory of the run, so
    the task would write somewhere other than the machine path the value is
    meant to name, and nothing would say so.
    """

    text = str(value)
    if not text.strip():
        raise ValueRuleError(f"{name} must be a non-empty path")
    if not text.startswith("/"):
        raise ValueRuleError(f"{name} must be an absolute path")
    return text


def check_nonempty_text_tuple(value: object, name: str) -> tuple[str, ...]:
    """A non-empty tuple of non-empty texts, such as a command.

    An empty tuple is a command that runs nothing, and an empty word is a
    missing argument of the tool rather than a part of the command.
    """

    if not isinstance(value, tuple) or not value:
        raise ValueRuleError(f"{name} must be a non-empty tuple of texts")
    for word in value:
        if not isinstance(word, str) or not word.strip():
            raise ValueRuleError(f"{name} must hold non-empty texts")
    return value


def check_file_mode(value: object, name: str) -> int:
    """A file mode: a whole number with a permission bit, in chmod range.

    A mode of zero leaves the file unusable for everyone without saying a
    word, which is why it is not left to the generic rule of the annotation,
    where an int is only asked not to be negative.
    """

    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueRuleError(f"{name} must be a file mode above zero")
    if value > 0o7777:
        raise ValueRuleError(f"{name} must be a mode chmod accepts")
    return value


# A virtual package name is a name dpkg-query cannot see, so the real package
# that provides the tool must stand in the list instead. One pair per trap: the
# virtual name and the real package that provides it.
VIRTUAL_PACKAGE_NAMES: tuple[tuple[str, str], ...] = (
    ("exiftool", "libimage-exiftool-perl"),
)


def check_real_package_names(value: object, name: str) -> tuple[str, ...]:
    """Package names dpkg-query can see, and the real package of each trap.

    A virtual name looks missing on every run, so the task would reinstall it
    forever and never reach its goal; the real package that provides the tool
    must stand in the list instead, and the tool must still be in the list
    under that real name.
    """

    packages = check_nonempty_text_tuple(value, name)
    for virtual_name, real_name in VIRTUAL_PACKAGE_NAMES:
        if virtual_name in packages:
            raise ValueRuleError(
                f"{name} names the virtual package {virtual_name}, which "
                f"dpkg-query cannot see, so the task would reinstall it on "
                f"every run; name its real package {real_name}"
            )
        if real_name not in packages:
            raise ValueRuleError(
                f"{name} does not name {real_name}, the real package that "
                f"provides {virtual_name}"
            )
    return packages


def check_vault_entry_title(value: object, name: str) -> str:
    """The title must name an entry of the vault structure.

    The loader refused a title that no entry of the structure carries, and such
    a title would leave the runtime vault password unreadable on the machine
    while the task looked finished. With the values in modules the rule lives
    here instead of in the loader.
    """

    from pyntara.values import vault_structure

    title = check_nonempty_text(value, name)
    titles = {entry.title for entry in vault_structure.ENTRIES}
    if title not in titles:
        raise ValueRuleError(f"{name} names no entry of the vault structure: {title}")
    return title


def check_shipped_value(value: object, annotation: object, name: str) -> None:
    """Apply the rule the annotation of a value asks for.

    A text must have something in it, a whole number must not be negative, a
    path must be absolute, and a tuple must hold something whose elements pass
    the rule of the element type. An annotation this layer does not read, such
    as a float, a bool or a record type, is left alone: the generic pass never
    judges what it cannot read, and such a value belongs to the second layer
    only when a wrong one would be silent.
    """

    if annotation is str:
        check_nonempty_text(value, name)
        return
    if annotation is int:
        check_not_negative_int(value, name)
        return
    if annotation is Path:
        check_absolute_path(value, name)
        return
    if get_origin(annotation) is tuple:
        arguments = get_args(annotation)
        element = arguments[0] if arguments else None
        if element is str:
            check_nonempty_text_tuple(value, name)
            return
        if not isinstance(value, tuple) or not value:
            raise ValueRuleError(f"{name} must be a non-empty tuple")
