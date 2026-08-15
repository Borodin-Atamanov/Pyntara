"""Unit tests for shared helpers in utils.py."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from support import FakeProc as _FakeProc

from pyntara.utils import (
    proquint_decode,
    proquint_encode,
    run_command,
    service_is_active,
    service_is_enabled,
    trim_whitespace,
)


def test_run_command_merges_extra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=1800, extra_env={"DEBIAN_FRONTEND": "noninteractive"})
    env = captured["kwargs"]["env"]
    assert isinstance(env, dict)
    assert env["DEBIAN_FRONTEND"] == "noninteractive"


def test_run_command_applies_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=42)
    assert captured["kwargs"]["timeout"] == 42


def test_run_command_streams_by_default_and_captures_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=1800)
    run_command(["true"], timeout=1800, capture=True)
    assert "capture_output" not in captured[0] or captured[0]["capture_output"] is False
    assert captured[1]["capture_output"] is True


def test_run_command_feeds_stdin_when_input_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["cat"], timeout=1800, input="payload")
    assert captured["kwargs"]["input"] == "payload"


def test_run_command_omits_input_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=1800)
    # Without input the subprocess default (None) is used, so the
    # explicit argument never carries a payload.
    assert captured["kwargs"].get("input") is None


class ProquintTests:
    """Proquint encode/decode behavior (draft-rayner-proquint)."""

    def test_encode_empty_data_is_empty_string(self) -> None:
        assert proquint_encode(b"") == ""

    def test_encode_fixed_vectors(self) -> None:
        assert proquint_encode(b"\x00\x01") == "babad"
        assert proquint_encode(b"\x00\x00") == "babab"
        assert proquint_encode(b"\xff\xff") == "zuzuz"

    def test_encode_canonical_standard_vectors(self) -> None:
        assert proquint_encode(bytes([0x7F, 0x00, 0x00, 0x01])) == "lusab-babad"
        assert proquint_encode(bytes([0x3F, 0x54, 0xDC, 0xC1])) == "gutih-tugad"

    def test_encode_odd_length_appends_trailing_marker(self) -> None:
        assert proquint_encode(b"\x01") == "bahab-"

    def test_encode_separators_join_syllables(self) -> None:
        data = bytes([0x7F, 0x00, 0x00, 0x01])
        assert proquint_encode(data, "") == "lusabbabad"
        assert proquint_encode(data, "~") == "lusab~babad"
        assert proquint_encode(data, "::") == "lusab::babad"

    def test_encode_separator_ending_in_dash_keeps_marker_distinct(self) -> None:
        assert proquint_encode(b"\x00\x01\x00\x01", "--") == "babad--babad"
        assert proquint_encode(b"\x00\x01\x00", "--") == "babad--babab-"

    def test_encode_bytes_like_accepts_only_bytes(self) -> None:
        with pytest.raises(TypeError):
            proquint_encode("\x00\x01")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            proquint_encode(None)  # type: ignore[arg-type]

    def test_encode_separator_type_and_alphabet_validation(self) -> None:
        with pytest.raises(TypeError):
            proquint_encode(b"\x00\x01", 123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            proquint_encode(b"\x00\x01", "ab")
        with pytest.raises(ValueError):
            proquint_encode(b"\x00\x01", "ba")
        with pytest.raises(ValueError):
            proquint_encode(b"\x00\x01", "-a-")

    def test_decode_empty_and_whitespace_only_strings(self) -> None:
        assert proquint_decode("") == b""
        assert proquint_decode("   ") == b""

    def test_decode_fixed_vectors(self) -> None:
        assert proquint_decode("babad") == b"\x00\x01"
        assert proquint_decode("BABAD") == b"\x00\x01"
        assert proquint_decode("babab") == b"\x00\x00"
        assert proquint_decode("zuzuz") == b"\xff\xff"

    def test_decode_separators_and_junk_are_ignored(self) -> None:
        expected = bytes([0x7F, 0x00, 0x00, 0x01])
        assert proquint_decode("lusab-babad") == expected
        assert proquint_decode("lu-sab ba-bad") == expected
        assert proquint_decode("lusabbabad") == expected
        assert proquint_decode("0q-lusab-babad") == expected
        assert proquint_decode("lusa0b-!babad") == expected

    def test_decode_trailing_marker_consumes_padding_byte(self) -> None:
        assert proquint_decode("babad-") == b"\x00"
        assert proquint_decode("babad-\n") == b"\x00"
        assert proquint_decode("babad- ") == b"\x00"

    def test_decode_trailing_marker_requires_padding(self) -> None:
        assert proquint_decode("babad--") is None
        assert proquint_decode("babadbabad-") is None

    def test_decode_positional_validation(self) -> None:
        assert proquint_decode("aabab") is None
        assert proquint_decode("babaa") is None
        assert proquint_decode("babadx") is None

    def test_decode_non_multiple_length_is_none(self) -> None:
        assert proquint_decode("babadab") is None

    def test_decode_junk_only_strings_are_none(self) -> None:
        assert proquint_decode("abc") is None
        assert proquint_decode("!!!") is None
        assert proquint_decode("-") is None
        assert proquint_decode("0q-") is None

    def test_decode_type_error_on_non_string(self) -> None:
        with pytest.raises(TypeError):
            proquint_decode(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            proquint_decode(None)  # type: ignore[arg-type]

    def test_round_trip_with_separators(self) -> None:
        for separator in ("-", "", "~", "::", "--"):
            for length in range(65):
                data = bytes(range(length))
                assert proquint_decode(proquint_encode(data, separator)) == data

    def test_round_trip_large_input(self) -> None:
        data = bytes(range(256)) * 16
        assert proquint_decode(proquint_encode(data)) == data

    def test_decode_junk_corpus_never_raises(self) -> None:
        junk = ["123", "!@#", "babad 0x01", "\u00df\u00e9\u4e2d", "ab--cd", "x-y-z"]
        for item in junk:
            result = proquint_decode(item)
            assert result is None or isinstance(result, bytes)


@pytest.mark.parametrize(
    "output,expected",
    [
        ("enabled\n", True),
        ("enabled-runtime\n", True),
        ("disabled\n", False),
        ("", False),
    ],
)
def test_service_is_enabled_matches_only_enabled(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: bool
) -> None:
    # The exact "enabled" and "enabled-runtime" states mean the service
    # starts at boot; every other state does not.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        assert command == ["systemctl", "is-enabled", "svc.service"]
        assert kwargs["check"] is False
        return _FakeProc(0 if output in ("enabled\n", "enabled-runtime\n") else 1, output)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    assert service_is_enabled("svc.service", timeout=5) is expected


@pytest.mark.parametrize(
    "output,expected",
    [
        ("active\n", True),
        ("inactive\n", False),
        ("failed\n", False),
    ],
)
def test_service_is_active_matches_only_active(
    monkeypatch: pytest.MonkeyPatch, output: str, expected: bool
) -> None:
    # Only the exact "active" state means the service is running.
    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        assert command == ["systemctl", "is-active", "svc.service"]
        assert kwargs["check"] is False
        return _FakeProc(0 if output == "active\n" else 1, output)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    assert service_is_active("svc.service", timeout=5) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", ""),
        ("   \n\t\n  ", ""),
        ("  text  ", "text"),
        ("\n\t text \n", "text"),
        ("line one\nline two\n", "line one\nline two"),
        ("  \nfirst\n\nlast\n  ", "first\n\nlast"),
    ],
)
def test_trim_whitespace_removes_edges_only(text: str, expected: str) -> None:
    # Leading and trailing whitespace is removed; everything between the
    # edges, including internal newlines, is preserved.
    assert trim_whitespace(text) == expected

