"""Unit tests for the line-level config editing helpers.

The helpers work on plain text and on files in temporary directories; no
external resources are involved, so the tests need no fakes
(docs/guides/developer-guide.md).
"""

from __future__ import annotations

from pathlib import Path

from pyntara.config_edit import add_line_to_file, replace_line_by_string


def test_replaces_line_containing_needle() -> None:
    text = "a = 1\nb = 2\nc = 3\n"
    new_text, changed = replace_line_by_string(text, "b =", "b = 99")
    assert changed is True
    assert new_text == "a = 1\nb = 99\nc = 3\n"


def test_keeps_unrelated_lines_and_comments() -> None:
    text = "# comment\nb = 2\nc = 3\n"
    new_text, changed = replace_line_by_string(text, "b =", "b = 99")
    assert changed is True
    assert new_text == "# comment\nb = 99\nc = 3\n"


def test_stop_word_line_is_untouched() -> None:
    # The commented b line is protected by the stop word, so the slide is
    # appended instead of replacing it.
    text = "a = 1\n# b = 2\nc = 3\n"
    new_text, changed = replace_line_by_string(text, "b =", "b = 99", stop_word="#")
    assert changed is True
    assert new_text == "a = 1\n# b = 2\nc = 3\nb = 99\n"


def test_fuzzy_slide_line_is_normalized() -> None:
    # A line that contains the slide is replaced by the exact slide.
    text = "a = 1\nx = 2 extra\nc = 3\n"
    new_text, changed = replace_line_by_string(text, "b =", "x = 2")
    assert changed is True
    assert new_text == "a = 1\nx = 2\nc = 3\n"


def test_exact_slide_line_is_not_duplicated() -> None:
    # The slide is already present as an exact line: nothing changes and
    # nothing is appended.
    text = "a = 1\nx = 2\nc = 3\n"
    new_text, changed = replace_line_by_string(text, "b =", "x = 2")
    assert changed is False
    assert new_text == text


def test_appends_slide_when_missing() -> None:
    text = "a = 1\nc = 3\n"
    new_text, changed = replace_line_by_string(text, "b =", "b = 2")
    assert changed is True
    assert new_text == "a = 1\nc = 3\nb = 2\n"


def test_no_append_when_add_slide_disabled() -> None:
    text = "a = 1\nc = 3\n"
    new_text, changed = replace_line_by_string(
        text, "b =", "b = 2", add_slide_if_no_needle=False
    )
    assert changed is False
    assert new_text == text


def test_unchanged_text_without_trailing_newline_stays_identical() -> None:
    # A file without a trailing newline must stay byte-identical when
    # nothing changed, so no-op detection is truthful.
    text = "a = 1\nb = 2"
    new_text, changed = replace_line_by_string(
        text, "z =", "z = 9", add_slide_if_no_needle=False
    )
    assert changed is False
    assert new_text == text


def test_empty_text_appends_slide() -> None:
    new_text, changed = replace_line_by_string("", "b =", "b = 2")
    assert changed is True
    assert new_text == "b = 2\n"


def test_add_line_to_file_appends_missing_line(tmp_path: Path) -> None:
    path = tmp_path / "config.conf"
    path.write_text("a = 1\nc = 3\n", encoding="utf-8")
    assert add_line_to_file(path, "b = 2") is True
    assert path.read_text(encoding="utf-8") == "a = 1\nc = 3\nb = 2\n"


def test_add_line_to_file_existing_line_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "config.conf"
    path.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    before = path.read_bytes()
    assert add_line_to_file(path, "b = 2") is False
    assert path.read_bytes() == before


def test_add_line_to_file_normalizes_fuzzy_line(tmp_path: Path) -> None:
    path = tmp_path / "config.conf"
    path.write_text("b = 2 extra\n", encoding="utf-8")
    assert add_line_to_file(path, "b = 2") is True
    assert path.read_text(encoding="utf-8") == "b = 2\n"


def test_add_line_to_file_keeps_commented_line(tmp_path: Path) -> None:
    # The commented b line is protected by the comment sign: it stays and
    # the exact line is appended.
    path = tmp_path / "config.conf"
    path.write_text("# b = 2\n", encoding="utf-8")
    assert add_line_to_file(path, "b = 2") is True
    assert path.read_text(encoding="utf-8") == "# b = 2\nb = 2\n"


def test_add_line_to_file_missing_file_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "config.conf"
    assert add_line_to_file(path, "b = 2") is False
    assert not path.exists()
