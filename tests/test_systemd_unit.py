"""Unit tests for the shared systemd unit helpers.

The ExecStart line of a unit is built by exec_start_line, so the rule that keeps
an argument alive stands in one place and every task that renders a command line
into a unit uses it. The rule is measured and not assumed: systemd splits the
value of ExecStart on whitespace, so a plain join makes an empty argument
disappear and moves the argument after it into its place, which makes a program
parameter the value of the parameter before it (probe unit, 2026-10-14).
"""

from __future__ import annotations

from pyntara.systemd_unit import exec_start_line


def test_a_plain_argument_is_left_as_it_is() -> None:
    # The ordinary case: no argument of the vector needs quoting, so the line a
    # person reads in the unit file stays plain and readable.
    line = exec_start_line(["/usr/bin/program", "--mode", "600"])
    assert line == "ExecStart=/usr/bin/program --mode 600"


def test_an_empty_argument_is_quoted_so_it_survives() -> None:
    # An argument the caller passed as an empty string is a value of the program
    # and not a gap: unquoted it leaves the line and the argument after it takes
    # its place, so the program fails on its own parser and the unit never
    # starts, which is what happened to a swap service on 2026-10-14.
    line = exec_start_line(["/usr/bin/program", "--resume-device", "", "--force"])
    assert line == 'ExecStart=/usr/bin/program --resume-device "" --force'


def test_an_argument_with_a_space_is_quoted() -> None:
    # A space inside an argument splits it into two arguments the same way, so it
    # is quoted as well.
    line = exec_start_line(["/usr/bin/program", "--label", "two words"])
    assert line == 'ExecStart=/usr/bin/program --label "two words"'


def test_a_quote_inside_an_argument_is_escaped() -> None:
    # systemd reads the double quotes of the line itself, so a quote that belongs
    # to the argument is escaped and reaches the program as the character the
    # caller passed instead of ending the quoted argument early.
    line = exec_start_line(["/usr/bin/program", 'say "hi"'])
    assert line == 'ExecStart=/usr/bin/program "say \\"hi\\""'


def test_a_backslash_inside_an_argument_is_escaped() -> None:
    line = exec_start_line(["/usr/bin/program", "a\\b"])
    assert line == 'ExecStart=/usr/bin/program "a\\\\b"'
