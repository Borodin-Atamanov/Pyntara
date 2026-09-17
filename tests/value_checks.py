"""Rules of the task values, test side only.

A value is a typed constant of pyntara.values, so its type is checked by
mypy before any run and needs no rule here. What lives here is the part a
type cannot say: the shape a value must have beyond its type, and the
cross-checks between values. The rules of the shipped values are applied
by tests/test_values.py, which is also where a rule that a value breaks
fails the suite during development instead of on a machine.

Every rule takes the value and the dotted name it is reported under,
returns the value unchanged when the rule holds and raises ValueRuleError
when it does not, so a test feeds a rule a bad value without touching the
shipped ones. The rules are shared by every section, because a rule about
the shape of a text or a count is the same rule everywhere.
"""

from __future__ import annotations


class ValueRuleError(RuntimeError):
    """Raised when a shipped value breaks a rule that its type cannot say."""


def check_nonempty_text(value: object, name: str) -> str:
    """A text with something in it.

    An empty text is the shape a value takes when it is declared and never
    filled in: a path that names nothing, a file name that matches no file,
    a suffix that names no suffix.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueRuleError(f"{name} must be a non-empty text")
    return value


def check_absolute_path(value: object, name: str) -> str:
    """A non-empty text that names an absolute path.

    A relative path would be resolved against the working directory of the
    run, so the task would write somewhere other than the machine path the
    value is meant to name.
    """

    text = check_nonempty_text(value, name)
    if not text.startswith("/"):
        raise ValueRuleError(f"{name} must be an absolute path")
    return text


def check_positive_int(value: object, name: str) -> int:
    """A whole number above zero.

    A boolean is refused although Python counts it as a whole number,
    because it is not a count; zero is refused because a count of zero
    means no attempt at all.
    """

    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueRuleError(f"{name} must be a positive integer")
    return value


def check_nonnegative_int(value: object, name: str) -> int:
    """A whole number of zero or more, such as a retry count."""

    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueRuleError(f"{name} must not be negative")
    return value


def check_file_mode(value: object, name: str) -> int:
    """A file mode: a whole number with a permission bit, in chmod range.

    A mode of zero leaves the file unusable for everyone, and a value above
    the twelve bits chmod accepts is not a mode at all.
    """

    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueRuleError(f"{name} must be a file mode above zero")
    if value > 0o7777:
        raise ValueRuleError(f"{name} must be a mode chmod accepts")
    return value


def check_text_tuple(value: object, name: str) -> tuple[str, ...]:
    """A non-empty tuple of non-empty texts, such as a command.

    An empty tuple is a command that runs nothing; an empty word is a
    missing argument of the tool rather than a part of the command.
    """

    if not isinstance(value, tuple) or not value:
        raise ValueRuleError(f"{name} must be a non-empty tuple of texts")
    for word in value:
        if not isinstance(word, str) or not word.strip():
            raise ValueRuleError(f"{name} must hold non-empty texts")
    return value
