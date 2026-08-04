"""Unit tests for shared helpers in utils.py."""

from __future__ import annotations

from pyntara.utils import parse_commented_lines


def test_parse_commented_lines_ignores_blank_lines_and_comments() -> None:
    raw = """
    # file managers
    mc
    nnn

    # system information
    htop
    """
    assert parse_commented_lines(raw) == ["mc", "nnn", "htop"]


def test_parse_commented_lines_strips_whitespace() -> None:
    assert parse_commented_lines("  mc  \n\thtop\n") == ["mc", "htop"]


def test_parse_commented_lines_returns_empty_for_blank_and_comment_only() -> None:
    assert parse_commented_lines("") == []
    assert parse_commented_lines("# only comments\n\n") == []


def test_parse_commented_lines_handles_comment_with_leading_space() -> None:
    assert parse_commented_lines("  # indented comment\nmc\n") == ["mc"]
