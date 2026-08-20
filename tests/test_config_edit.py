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


def test_sync_directives_creates_file_with_header_section_and_directives(
    tmp_path: Path,
) -> None:
    # A missing file is created with the header, the section and the
    # directives in order.
    from pyntara.config_edit import sync_directives_by_key

    path = tmp_path / "dropin.conf"
    assert (
        sync_directives_by_key(
            path,
            ("DNS=1.1.1.1", "DNSOverTLS=yes"),
            "# managed",
            "[Resolve]",
        )
        is True
    )
    assert path.read_text(encoding="utf-8") == (
        "# managed\n[Resolve]\nDNS=1.1.1.1\nDNSOverTLS=yes\n"
    )


def test_sync_directives_replaces_by_key_and_keeps_foreign_lines(
    tmp_path: Path,
) -> None:
    # A directive with the same key is replaced, a foreign line and a
    # commented line survive, and a missing directive is appended.
    from pyntara.config_edit import sync_directives_by_key

    path = tmp_path / "dropin.conf"
    path.write_text(
        "# managed\n[Resolve]\nDNS=old\nFallbackDNS=9.9.9.9\nCache=no\n",
        encoding="utf-8",
    )
    assert (
        sync_directives_by_key(
            path,
            ("DNS=1.1.1.1", "DNSOverTLS=yes"),
            "# managed",
            "[Resolve]",
        )
        is True
    )
    content = path.read_text(encoding="utf-8")
    assert "DNS=1.1.1.1\n" in content
    assert "DNS=old\n" not in content
    assert "FallbackDNS=9.9.9.9\n" in content
    assert "Cache=no\n" in content
    assert "DNSOverTLS=yes\n" in content


def test_sync_directives_does_not_clobber_similar_keys(tmp_path: Path) -> None:
    # Replacing DNS= must never touch FallbackDNS=, even though the key
    # FallbackDNS contains the substring DNS: the merge compares the full
    # key before the equals sign.
    from pyntara.config_edit import sync_directives_by_key

    path = tmp_path / "dropin.conf"
    path.write_text(
        "# managed\n[Resolve]\nFallbackDNS=9.9.9.9\nDNS=old\n",
        encoding="utf-8",
    )
    sync_directives_by_key(
        path,
        ("DNS=1.1.1.1",),
        "# managed",
        "[Resolve]",
    )
    content = path.read_text(encoding="utf-8")
    assert "FallbackDNS=9.9.9.9\n" in content
    assert "DNS=1.1.1.1\n" in content
    assert "DNS=old\n" not in content


def test_sync_directives_returns_false_when_unchanged(tmp_path: Path) -> None:
    # A file that already matches the directives is not rewritten.
    from pyntara.config_edit import sync_directives_by_key

    path = tmp_path / "dropin.conf"
    path.write_text(
        "# managed\n[Resolve]\nDNS=1.1.1.1\n", encoding="utf-8"
    )
    before = path.read_bytes()
    assert (
        sync_directives_by_key(
            path, ("DNS=1.1.1.1",), "# managed", "[Resolve]"
        )
        is False
    )
    assert path.read_bytes() == before


def test_sync_toml_root_directive_inserts_after_anchor(tmp_path: Path) -> None:
    # A missing root directive is inserted after the anchor line, so it
    # stays in the root table and never lands inside a later [section].
    from pyntara.config_edit import sync_toml_root_directive

    path = tmp_path / "config.toml"
    path.write_text(
        "listen_addresses = []\nserver_names = ['cloudflare']\n\n"
        "[query_log]\n  file = '/var/log/query.log'\n",
        encoding="utf-8",
    )
    assert (
        sync_toml_root_directive(
            path,
            "fallback_resolvers = ['1.1.1.1', '8.8.8.8']",
            "server_names = ['cloudflare']",
        )
        is True
    )
    content = path.read_text(encoding="utf-8")
    assert (
        "server_names = ['cloudflare']\n"
        "fallback_resolvers = ['1.1.1.1', '8.8.8.8']\n" in content
    )
    assert "[query_log]" in content


def test_sync_toml_root_directive_replaces_existing(tmp_path: Path) -> None:
    # An existing root directive is replaced in place, not duplicated.
    from pyntara.config_edit import sync_toml_root_directive

    path = tmp_path / "config.toml"
    path.write_text(
        "listen_addresses = []\nfallback_resolvers = ['old']\n",
        encoding="utf-8",
    )
    assert (
        sync_toml_root_directive(
            path,
            "fallback_resolvers = ['1.1.1.1']",
            "listen_addresses = []",
        )
        is True
    )
    content = path.read_text(encoding="utf-8")
    assert "fallback_resolvers = ['1.1.1.1']\n" in content
    assert "fallback_resolvers = ['old']\n" not in content


def test_sync_toml_root_directive_keeps_commented_line(tmp_path: Path) -> None:
    # A commented fallback_resolvers line is left untouched and the exact
    # directive is inserted after the anchor.
    from pyntara.config_edit import sync_toml_root_directive

    path = tmp_path / "config.toml"
    path.write_text(
        "# fallback_resolvers = ['old']\nserver_names = ['cloudflare']\n",
        encoding="utf-8",
    )
    assert (
        sync_toml_root_directive(
            path,
            "fallback_resolvers = ['1.1.1.1']",
            "server_names = ['cloudflare']",
        )
        is True
    )
    content = path.read_text(encoding="utf-8")
    assert "# fallback_resolvers = ['old']\n" in content
    assert "fallback_resolvers = ['1.1.1.1']\n" in content


def test_sync_toml_root_directive_inserts_before_section_when_anchor_missing(
    tmp_path: Path,
) -> None:
    # When the anchor is absent, the directive is inserted before the
    # first [section] so it stays in the root table.
    from pyntara.config_edit import sync_toml_root_directive

    path = tmp_path / "config.toml"
    path.write_text("[sources]\n  url = 'x'\n", encoding="utf-8")
    assert (
        sync_toml_root_directive(
            path,
            "fallback_resolvers = ['1.1.1.1']",
            "server_names = ['cloudflare']",
        )
        is True
    )
    content = path.read_text(encoding="utf-8")
    assert content.startswith("fallback_resolvers = ['1.1.1.1']\n[sources]")


def test_sync_toml_root_directive_returns_false_when_unchanged(
    tmp_path: Path,
) -> None:
    # A file that already carries the exact directive is not rewritten.
    from pyntara.config_edit import sync_toml_root_directive

    path = tmp_path / "config.toml"
    path.write_text(
        "fallback_resolvers = ['1.1.1.1']\n", encoding="utf-8"
    )
    before = path.read_bytes()
    assert (
        sync_toml_root_directive(
            path,
            "fallback_resolvers = ['1.1.1.1']",
            "server_names = ['cloudflare']",
        )
        is False
    )
    assert path.read_bytes() == before


def test_sync_toml_root_directive_missing_file_is_not_created(
    tmp_path: Path,
) -> None:
    from pyntara.config_edit import sync_toml_root_directive

    path = tmp_path / "config.toml"
    assert (
        sync_toml_root_directive(
            path,
            "fallback_resolvers = ['1.1.1.1']",
            "server_names = ['cloudflare']",
        )
        is False
    )
    assert not path.exists()
