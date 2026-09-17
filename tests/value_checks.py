"""Rules of the task values, test side only.

A value is a typed constant of pyntara.values, so its type is checked by
mypy before any run and needs no rule here. What lives here is the part a
type cannot say: the shape a value must have beyond its type, and the
cross-checks between values. The rules of the shipped values are applied
by tests/test_values.py, which is also where a rule that a value breaks
fails the suite during development instead of on a machine.

Every rule takes the value as an argument and returns it unchanged when it
holds, so a test can feed a rule a bad value without touching the shipped
ones.
"""

from __future__ import annotations


class ValueRuleError(RuntimeError):
    """Raised when a shipped value breaks a rule that its type cannot say."""


def check_hostname_file(value: object) -> str:
    """A non-empty path text: the file that holds the hostname.

    An empty path names no file, and the task that writes the hostname
    would write it nowhere.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueRuleError("hostname.HOSTNAME_FILE must be a non-empty path")
    return value


def check_random_bytes(value: object) -> int:
    """A positive whole number of random bytes.

    A count of zero or less encodes no name, so the task could not
    generate one. A boolean is refused although Python counts it as a
    whole number, because it is not a count.
    """

    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueRuleError("hostname.RANDOM_BYTES must be a positive integer")
    return value


def check_set_hostname_command(value: object) -> tuple[str, ...]:
    """A non-empty command of non-empty words that applies the hostname.

    An empty command applies nothing, and an empty word is a missing
    argument of the tool rather than a command part.
    """

    if not isinstance(value, tuple) or not value:
        raise ValueRuleError(
            "hostname.SET_HOSTNAME_COMMAND must be a non-empty command"
        )
    if not all(isinstance(word, str) and word.strip() for word in value):
        raise ValueRuleError(
            "hostname.SET_HOSTNAME_COMMAND must hold non-empty words"
        )
    return value
