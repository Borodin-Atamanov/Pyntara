"""Unit tests for shared helpers in utils.py."""

from __future__ import annotations

import re
import subprocess
from typing import Any

import pytest
from support import FakeProc as _FakeProc

from pyntara.utils import (
    curl_flags,
    ensure_port_free,
    port_listener_pid,
    proquint_decode,
    proquint_encode,
    run_command,
    service_is_active,
    service_is_enabled,
    service_main_pid,
    trim_whitespace,
)


def test_curl_flags_returns_retry_and_timeout_flags() -> None:
    flags = curl_flags(777, 13)
    assert flags == [
        "--max-time",
        "777",
        "--retry",
        "13",
        "--retry-delay",
        "3",
        "--retry-connrefused",
    ]


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


def test_run_command_logs_start_and_end_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 7, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["apt-get", "install", "-y", "python3-venv"], timeout=1800)
    captured = capsys.readouterr().out
    assert "  run : apt-get install -y python3-venv" in captured
    assert re.search(
        r"^  /run: 7 \d+\.\d{3}s apt-get install -y python3-venv$",
        captured,
        re.MULTILINE,
    )


def test_run_command_logs_capture_queries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["dpkg-query", "-W"], timeout=1800, capture=True)
    captured = capsys.readouterr().out
    assert "  run : dpkg-query -W" in captured
    assert re.search(
        r"^  /run: 0 \d+\.\d{3}s dpkg-query -W$", captured, re.MULTILINE
    )


def test_run_command_suppresses_log_on_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(
        ["curl", "--data-urlencode", "pass=secret"],
        timeout=1800,
        log_command=False,
    )
    captured = capsys.readouterr().out
    assert captured == ""


def test_run_command_logs_end_on_check_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(5, command)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        run_command(["apt-get", "install", "-y", "x"], timeout=1800)
    captured = capsys.readouterr().out
    assert "  run : apt-get install -y x" in captured
    assert re.search(
        r"^  /run: 5 \d+\.\d{3}s apt-get install -y x$",
        captured,
        re.MULTILINE,
    )


def test_run_command_mirrors_tracking_lines_to_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journaled: list[str] = []
    monkeypatch.setattr(
        "pyntara.logger._send_to_journal",
        lambda message, priority=6: journaled.append(message),
    )

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 3, "", "")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    run_command(["true"], timeout=1800)
    assert "run : true" in journaled
    assert any(
        re.match(r"^/run: 3 \d+\.\d{3}s true$", message)
        for message in journaled
    )


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


class TestPortFreeing:
    """Tests for the port-listener and port-freeing helpers."""

    def test_port_listener_pid_parses_ss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The pid is parsed from the process column of the ss output.
        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            assert command[0] == "ss"
            return _FakeProc(
                0, 'LISTEN 0 4096 *:35353 *:* users:(("x-ui",pid=34311,fd=11))\n'
            )

        monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
        assert port_listener_pid(35353, timeout=30) == 34311

    def test_port_listener_pid_none_when_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty ss output means the port is free.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(0, ""),
        )
        assert port_listener_pid(35353, timeout=30) is None

    def test_port_listener_pid_none_on_ss_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failed ss query is treated as a free port: the caller must
        # not mistake an unreadable state for an occupied one.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(7, ""),
        )
        assert port_listener_pid(35353, timeout=30) is None

    def test_service_main_pid_parses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The MainPID is parsed from systemctl show --value.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(0, "34311\n"),
        )
        assert service_main_pid("x-ui.service", timeout=30) == 34311

    def test_service_main_pid_none_when_stopped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A zero MainPID (stopped service) normalizes to None.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(0, "0\n"),
        )
        assert service_main_pid("x-ui.service", timeout=30) is None

    def test_ensure_port_free_free_port_does_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No listener on the port: no stop and no kill.
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(0, ""),
        )
        monkeypatch.setattr(
            "pyntara.utils.os.kill", lambda pid, sig: killed.append((pid, sig))
        )
        result = ensure_port_free(
            35353, "x-ui.service", timeout=30, service_process_name="x-ui"
        )
        assert result is None
        assert killed == []

    def test_ensure_port_free_stops_own_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The listener pid matches the service MainPID: systemctl stop
        # runs and the freed port is confirmed.
        calls: list[list[str]] = []
        ss_calls = 0

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(command)
            nonlocal ss_calls
            if command[0] == "ss":
                ss_calls += 1
                if ss_calls == 1:
                    return _FakeProc(
                        0,
                        'LISTEN 0 4096 *:35353 *:* '
                        'users:(("x-ui",pid=34311,fd=11))\n',
                    )
                return _FakeProc(0, "")
            if command[0] == "systemctl" and command[1] == "show":
                return _FakeProc(0, "34311\n")
            return _FakeProc(0)

        monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
        result = ensure_port_free(
            35353, "x-ui.service", timeout=30, service_process_name="x-ui"
        )
        assert result is not None
        assert "stopped x-ui.service" in result
        assert ["systemctl", "stop", "x-ui.service"] in calls

    def test_ensure_port_free_stops_by_process_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The MainPID does not match but the process name is x-ui: the
        # service is stopped anyway.
        calls: list[list[str]] = []
        ss_calls = 0

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            calls.append(command)
            nonlocal ss_calls
            if command[0] == "ss":
                ss_calls += 1
                if ss_calls == 1:
                    return _FakeProc(
                        0,
                        'LISTEN 0 4096 *:35353 *:* '
                        'users:(("x-ui",pid=999,fd=11))\n',
                    )
                return _FakeProc(0, "")
            if command[0] == "systemctl" and command[1] == "show":
                return _FakeProc(0, "0\n")
            return _FakeProc(0)

        monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
        monkeypatch.setattr("pyntara.utils.process_comm", lambda pid: "x-ui")
        result = ensure_port_free(
            35353, "x-ui.service", timeout=30, service_process_name="x-ui"
        )
        assert result is not None
        assert "stopped x-ui.service" in result
        assert ["systemctl", "stop", "x-ui.service"] in calls

    def test_ensure_port_free_terminates_unknown_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An unknown listener is terminated with SIGTERM; the freed port
        # is confirmed and no SIGKILL is needed.
        killed: list[tuple[int, int]] = []
        ss_calls = 0

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            nonlocal ss_calls
            if command[0] == "ss":
                ss_calls += 1
                if ss_calls == 1:
                    return _FakeProc(
                        0, 'LISTEN 0 4096 *:35353 *:* users:(("other",pid=999,fd=9))\n'
                    )
                return _FakeProc(0, "")
            if command[0] == "systemctl" and command[1] == "show":
                return _FakeProc(0, "0\n")
            return _FakeProc(0)

        monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
        monkeypatch.setattr(
            "pyntara.utils.os.kill", lambda pid, sig: killed.append((pid, sig))
        )
        result = ensure_port_free(
            35353, "x-ui.service", timeout=30, service_process_name="x-ui"
        )
        assert result is not None
        assert "terminated unknown process 999" in result
        assert killed == [(999, 15)]  # SIGTERM only

    def test_ensure_port_free_sigkills_when_grace_expires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The process ignores SIGTERM, so SIGKILL is sent after the grace
        # period and the port is freed.
        killed: list[tuple[int, int]] = []
        ss_calls = 0

        def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
            del kwargs
            nonlocal ss_calls
            if command[0] == "ss":
                ss_calls += 1
                if ss_calls == 1:
                    return _FakeProc(
                        0,
                        'LISTEN 0 4096 *:35353 *:* '
                        'users:(("other",pid=999,fd=9))\n',
                    )
                if ss_calls == 2:
                    return _FakeProc(
                        0,
                        'LISTEN 0 4096 *:35353 *:* '
                        'users:(("other",pid=999,fd=9))\n',
                    )
                return _FakeProc(0, "")
            if command[0] == "systemctl" and command[1] == "show":
                return _FakeProc(0, "0\n")
            return _FakeProc(0)

        monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
        monkeypatch.setattr(
            "pyntara.utils.os.kill", lambda pid, sig: killed.append((pid, sig))
        )
        monkeypatch.setattr("pyntara.utils.time.sleep", lambda _seconds: None)
        monotonic = iter([0.0, 0.0, 6.0])
        monkeypatch.setattr("pyntara.utils.time.monotonic", lambda: next(monotonic))
        result = ensure_port_free(
            35353, "x-ui.service", timeout=30, service_process_name="x-ui"
        )
        assert result is not None
        assert "killed unknown process 999" in result
        assert killed == [(999, 15), (999, 9)]  # SIGTERM then SIGKILL

    def test_ensure_port_free_raises_when_still_occupied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even SIGKILL does not free the port: the helper raises.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(
                0,
                'LISTEN 0 4096 *:35353 *:* users:(("other",pid=999,fd=9))\n',
            )
            if command[0] == "ss"
            else _FakeProc(0, "0\n"),
        )
        monkeypatch.setattr(
            "pyntara.utils.os.kill", lambda pid, sig: None
        )
        monkeypatch.setattr("pyntara.utils.time.sleep", lambda _seconds: None)
        monotonic = iter([0.0, 0.0, 6.0])
        monkeypatch.setattr("pyntara.utils.time.monotonic", lambda: next(monotonic))
        with pytest.raises(RuntimeError, match="still listens"):
            ensure_port_free(
                35353, "x-ui.service", timeout=30, service_process_name="x-ui"
            )

