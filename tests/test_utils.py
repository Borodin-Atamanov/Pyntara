"""Unit tests for shared helpers in utils.py."""

from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from support import FakeProc as _FakeProc
from support import make_config

from pyntara import utils
from pyntara.utils import (
    curl_flags,
    ensure_port_free,
    fetch_urls_in_parallel,
    port_listener_pid,
    proquint_decode,
    proquint_encode,
    run_command,
    service_is_active,
    service_is_enabled,
    service_main_pid,
    trim_whitespace,
    version_without_tag_prefix,
)

# Two URLs for the parallel query tests: the shape of the addresses does
# not matter there, only that both transfers run in one call.
URLS = ("https://api4.ipify.org", "https://ipv6.ipify.org")


class _FakePopen:
    """A Popen double that captures the pipe output of one curl call.

    communicate records the timeout it was given and returns the fixed
    output; with kill_before_output the first call raises TimeoutExpired,
    so the caller must kill the process before the output appears.
    """

    def __init__(self, output: str = "", kill_before_output: bool = False) -> None:
        self.output = output
        self.kill_before_output = kill_before_output
        self.killed = False
        self.timeout_used: float | None = None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.timeout_used = timeout
        if self.kill_before_output and not self.killed:
            raise subprocess.TimeoutExpired("curl", timeout or 0)
        return (self.output, "")

    def kill(self) -> None:
        self.killed = True


def test_curl_flags_returns_retry_and_timeout_flags() -> None:
    flags = curl_flags(777, 17, 60, 7777, 3)
    assert flags == [
        "--max-time",
        "777",
        "--connect-timeout",
        "60",
        "--retry",
        "17",
        "--retry-all-errors",
        "--retry-delay",
        "3",
        "--retry-max-time",
        "7777",
        "--retry-connrefused",
    ]


def test_download_command_fills_the_template_and_appends_the_url(
    tmp_path: Path,
) -> None:
    # The configured download template is filled from the values of the
    # call, the retry flags of the engine follow it and the URL closes the
    # command, so every task downloads with one definition.
    engine = make_config().engine
    command = utils.download_command(
        engine, tmp_path / "archive.tar.gz", "https://example.invalid/a.tar.gz"
    )
    assert command[:2] == ["curl", "--fail"]
    assert command[command.index("--output") + 1] == str(
        tmp_path / "archive.tar.gz"
    )
    assert command[command.index("--write-out") + 1] == (
        engine.curl_download_write_out
    )
    assert "--retry" in command
    assert str(engine.curl_download_timeout_seconds) in command
    assert command[-1] == "https://example.invalid/a.tar.gz"
    assert "{output_path}" not in command


def test_release_query_command_uses_the_query_template() -> None:
    # The release query is the configured query call plus the retry flags
    # and the URL, so every task asks a release API the same way.
    engine = make_config().engine
    command = utils.release_query_command(
        engine, "https://api.example.invalid/releases/latest"
    )
    assert command[: len(engine.curl_query_command)] == list(
        engine.curl_query_command
    )
    assert "--silent" in command
    assert "--retry" in command
    assert command[-1] == "https://api.example.invalid/releases/latest"


def test_os_family_is_debian_reads_the_configured_vocabulary() -> None:
    # The family fields and the accepted values come from the engine: a
    # derivative that declares the family in ID_LIKE is accepted, a value
    # outside the configured list is not, and a rearranged vocabulary is
    # honoured without touching the code.
    engine = make_config().engine
    assert utils.os_family_is_debian(
        engine, {"ID": "ubuntu"}
    )
    assert utils.os_family_is_debian(
        engine, {"ID_LIKE": "ubuntu debian"}
    )
    assert not utils.os_family_is_debian(engine, {"ID": "arch"})
    narrowed = replace(engine, os_release_debian_family_names=("ubuntu",))
    assert not utils.os_family_is_debian(narrowed, {"ID": "debian"})
    renamed = replace(engine, os_release_family_keys=("FAMILY",))
    assert utils.os_family_is_debian(renamed, {"FAMILY": "debian"})
    assert not utils.os_family_is_debian(renamed, {"ID": "debian"})


def test_curl_command_refuses_an_unknown_placeholder() -> None:
    # A template with a placeholder nobody fills fails loudly instead of
    # running a command with a literal brace in it.
    engine = make_config().engine
    with pytest.raises(KeyError):
        utils.curl_command(
            engine,
            ("curl", "--output", "{wrong_placeholder}"),
            "https://example.invalid",
            timeout_seconds=1.0,
            substitutions={"output_path": "/tmp/out"},
        )


def test_package_status_query_comes_from_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The status query is a config value: the shipped template doubles the
    # braces of the literal ${Status}, because the substitution helper
    # formats the command as a template, so the run receives the single
    # braces below. Another argv in the engine table is exactly what the
    # helper runs, so a derivative that queries packages differently edits
    # only the config.
    engine = make_config().engine
    assert engine.package_status_query_command == (
        "dpkg-query",
        "-W",
        "-f=${{Status}}",
        "{package}",
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> _FakeProc:
        calls.append(list(command))
        return _FakeProc(0, "install ok installed")

    monkeypatch.setattr(utils, "run_command", fake_run)
    assert utils.package_is_installed(engine, "mc", 5.0) is True
    assert calls == [["dpkg-query", "-W", "-f=${Status}", "mc"]]

    replaced = replace(
        engine, package_status_query_command=("myquery", "{package}", "-s")
    )
    assert utils.package_is_installed(replaced, "nc", 5.0) is True
    assert calls[-1] == ["myquery", "nc", "-s"]


def test_package_is_installed_needs_the_installed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A leftover configuration is not an installed package and a failing
    # query is not either: the helper needs the installed status line.
    engine = make_config().engine
    monkeypatch.setattr(
        utils,
        "run_command",
        lambda *_args, **_kwargs: _FakeProc(0, "deinstall ok config-files"),
    )
    assert utils.package_is_installed(engine, "mc", 5.0) is False
    monkeypatch.setattr(
        utils,
        "run_command",
        lambda *_args, **_kwargs: _FakeProc(1, "install ok installed"),
    )
    assert utils.package_is_installed(engine, "mc", 5.0) is False


def test_apt_calls_come_from_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    # The install of one package, the index refresh and the noninteractive
    # environment are config values: the helpers run exactly the configured
    # argv with the configured environment, so a derivative that installs
    # packages another way edits only the config.
    engine = make_config().engine
    calls: list[list[str]] = []
    envs: list[dict[str, str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        calls.append(list(command))
        extra_env = kwargs.get("extra_env")
        envs.append(dict(extra_env) if isinstance(extra_env, dict) else {})
        return _FakeProc(0)

    monkeypatch.setattr(utils, "run_command", fake_run)
    assert utils.install_package_once(engine, "mc", 30.0) == (True, "")
    assert calls == [["apt-get", "install", "-y", "mc"]]
    assert envs == [{"DEBIAN_FRONTEND": "noninteractive"}]
    utils.refresh_apt_index(engine, 30.0)
    assert calls[-1] == ["apt-get", "update"]

    marker = replace(
        engine,
        apt_install_command=("myinstall", "{package}", "--yes"),
        apt_update_command=("myupdate",),
        apt_noninteractive_environment={"APT_ANSWER": "always"},
    )
    assert utils.install_package_once(marker, "nc", 30.0) == (True, "")
    assert calls[-1] == ["myinstall", "nc", "--yes"]
    assert envs[-1] == {"APT_ANSWER": "always"}
    utils.refresh_apt_index(marker, 30.0)
    assert calls[-1] == ["myupdate"]


def test_install_packages_refreshes_once_and_installs_each_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One refresh for the whole list, one install per package, and no
    # refresh when the run asked to skip it.
    engine = make_config().engine
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> _FakeProc:
        calls.append(list(command))
        return _FakeProc(0)

    monkeypatch.setattr(utils, "run_command", fake_run)
    installed, failures, warnings = utils.install_packages(
        engine,
        ["mc", "nc"],
        install_timeout=30.0,
        update_timeout=30.0,
        retries=0,
        skip_update=False,
    )
    assert (installed, failures, warnings) == (["mc", "nc"], [], [])
    assert calls[0] == ["apt-get", "update"]
    assert calls[1:] == [
        ["apt-get", "install", "-y", "mc"],
        ["apt-get", "install", "-y", "nc"],
    ]

    calls.clear()
    utils.install_packages(
        engine,
        ["mc"],
        install_timeout=30.0,
        update_timeout=30.0,
        retries=0,
        skip_update=True,
    )
    assert calls == [["apt-get", "install", "-y", "mc"]]


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
    assert (
        service_is_enabled(make_config().engine, "svc.service", timeout=5)
        is expected
    )


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
    assert (
        service_is_active(make_config().engine, "svc.service", timeout=5)
        is expected
    )


def test_service_state_queries_come_from_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The two queries and the states that count as enabled or running are
    # config values: another argv and another state word are honoured, so a
    # derivative that spells them differently edits only the config.
    engine = replace(
        make_config().engine,
        systemctl_is_enabled_command=("myctl", "boot-state", "{unit}"),
        systemctl_is_active_command=("myctl", "run-state", "{unit}"),
        systemd_enabled_states=("booted",),
        systemd_active_state="running",
    )
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        seen.append(list(command))
        state = "booted\n" if command[1] == "boot-state" else "running\n"
        return _FakeProc(0, state)

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    assert service_is_enabled(engine, "svc.service", timeout=5) is True
    assert service_is_active(engine, "svc.service", timeout=5) is True
    assert seen == [
        ["myctl", "boot-state", "svc.service"],
        ["myctl", "run-state", "svc.service"],
    ]


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
        assert port_listener_pid(make_config().engine, 35353, timeout=30) == 34311

    def test_port_listener_pid_none_when_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty ss output means the port is free.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(0, ""),
        )
        assert port_listener_pid(make_config().engine, 35353, timeout=30) is None

    def test_port_listener_pid_none_on_ss_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failed ss query is treated as a free port: the caller must
        # not mistake an unreadable state for an occupied one.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(7, ""),
        )
        assert port_listener_pid(make_config().engine, 35353, timeout=30) is None

    def test_service_main_pid_parses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The MainPID is parsed from systemctl show --value.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(0, "34311\n"),
        )
        assert service_main_pid(make_config().engine, "x-ui.service", timeout=30) == 34311

    def test_service_main_pid_none_when_stopped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A zero MainPID (stopped service) normalizes to None.
        monkeypatch.setattr(
            "pyntara.utils.subprocess.run",
            lambda command, **kwargs: _FakeProc(0, "0\n"),
        )
        assert service_main_pid(make_config().engine, "x-ui.service", timeout=30) is None

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
            make_config().engine,
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
            make_config().engine,
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
            make_config().engine,
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
            make_config().engine,
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
            make_config().engine,
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
                make_config().engine,
                35353, "x-ui.service", timeout=30, service_process_name="x-ui"
            )


def test_port_and_main_pid_queries_come_from_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The listener query and the MainPID query are config values: another
    # argv in the engine table is exactly what runs, the port and the unit
    # fill the placeholders, so a derivative that queries them differently
    # edits only the config.
    engine = replace(
        make_config().engine,
        socket_listener_command=("myss", "--listen", "{port}"),
        systemctl_main_pid_command=("myctl", "main-pid", "{unit}"),
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if command[0] == "myss":
            return _FakeProc(
                0, 'LISTEN 0 4096 *:35353 *:* users:(("x-ui",pid=7,fd=11))\n'
            )
        return _FakeProc(0, "7\n")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    assert port_listener_pid(engine, 35353, timeout=30) == 7
    assert service_main_pid(engine, "x-ui.service", timeout=30) == 7
    assert calls == [
        ["myss", "--listen", "35353"],
        ["myctl", "main-pid", "x-ui.service"],
    ]


def test_the_stop_call_comes_from_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The stop of the managed service is a config value as well: with the
    # shipped table the sequence is the ss query, the MainPID query, the
    # stop and the confirming query of the port.
    engine = replace(
        make_config().engine,
        systemctl_stop_command=("myctl", "halt", "{unit}"),
    )
    calls: list[list[str]] = []
    listener_outputs = [
        'LISTEN 0 4096 *:35353 *:* users:(("x-ui",pid=34311,fd=11))\n',
        "",
    ]

    def fake_run(command: list[str], **kwargs: object) -> _FakeProc:
        del kwargs
        calls.append(list(command))
        if command[0] == "ss":
            return _FakeProc(0, listener_outputs.pop(0))
        return _FakeProc(0, "34311\n")

    monkeypatch.setattr("pyntara.utils.subprocess.run", fake_run)
    assert ensure_port_free(engine, 35353, "x-ui.service", 30) == (
        "stopped x-ui.service listening on port 35353"
    )
    assert calls == [
        ["ss", "-tlnp", "sport = :35353"],
        ["systemctl", "show", "-p", "MainPID", "--value", "x-ui.service"],
        ["myctl", "halt", "x-ui.service"],
        ["ss", "-tlnp", "sport = :35353"],
    ]


def test_repository_root_is_computed_once() -> None:
    # The root of the clone is computed by the composition root, which puts
    # it into the Context; a task reads it from there. A module that computes
    # its own copy, or mentions the constant at all, is one more place to
    # change and points elsewhere as soon as the file moves, so the suite
    # allows the composition root alone and refuses every other mention.
    src_root = Path(utils.__file__).resolve().parent
    composition_root = "pyntara.py"
    duplicated: list[str] = []
    mentioned: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        relative = str(path.relative_to(src_root))
        if relative == composition_root:
            continue
        text = path.read_text(encoding="utf-8")
        if "REPO_ROOT = " in text:
            duplicated.append(relative)
        if "REPO_ROOT" in text:
            mentioned.append(relative)
    assert not duplicated, f"modules computing their own REPO_ROOT: {duplicated}"
    assert not mentioned, (
        f"modules naming REPO_ROOT instead of the Context: {mentioned}"
    )
    definition = (src_root / composition_root).read_text(encoding="utf-8")
    assert "REPO_ROOT = " in definition, (
        "the composition root must compute the root of the clone"
    )


class TestFetchUrlsInParallel:
    """Tests for the one curl call that queries every URL at once.

    The process double lives here and not in support.py: the shared
    support module is edited by other work in this repository, and a test
    helper with one user does not belong there.
    """

    def test_queries_every_url_in_one_parallel_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every URL runs in the same curl process, so a slow service
        # cannot delay the others one by one.
        commands: list[list[str]] = []
        process = _FakePopen("first\nsecond\n")

        def fake_popen(command: list[str], **kwargs: Any) -> _FakePopen:
            commands.append(command)
            return process

        monkeypatch.setattr("pyntara.utils.subprocess.Popen", fake_popen)
        engine = make_config().engine
        assert fetch_urls_in_parallel(engine, URLS, 60, 1800.0) == "first\nsecond\n"
        command = commands[0]
        assert "--parallel" in command
        assert command[command.index("--max-time") + 1] == "60"
        assert command[-len(URLS) :] == list(URLS)
        assert engine.curl_parallel_write_out in command
        assert process.timeout_used == 1800.0

    def test_returns_nothing_when_the_url_list_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty list must not start a process at all.
        def fail_popen(command: list[str], **kwargs: Any) -> _FakePopen:
            raise AssertionError("no process expected")

        monkeypatch.setattr("pyntara.utils.subprocess.Popen", fail_popen)
        assert fetch_urls_in_parallel(make_config().engine, (), 60, 1800.0) == ""

    def test_returns_nothing_when_curl_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_popen(command: list[str], **kwargs: Any) -> _FakePopen:
            raise OSError("curl not found")

        monkeypatch.setattr("pyntara.utils.subprocess.Popen", fail_popen)
        assert fetch_urls_in_parallel(make_config().engine, URLS, 60, 1800.0) == ""

    def test_kills_the_process_when_the_command_timeout_expires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The command timeout bounds the call even when curl never ends.
        process = _FakePopen("answer\n", kill_before_output=True)
        monkeypatch.setattr(
            "pyntara.utils.subprocess.Popen", lambda *a, **k: process
        )
        assert fetch_urls_in_parallel(make_config().engine, URLS, 60, 1800.0) == "answer\n"
        assert process.killed is True


def test_apply_owner_applies_the_configured_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The owner is the uid and the gid pair the [engine] config carries, so
    # no module writes the owner of root itself.
    target = tmp_path / "owned"
    target.write_text("x", encoding="utf-8")
    chowned: list[tuple[object, int, int]] = []
    monkeypatch.setattr(utils.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        utils.os, "chown", lambda path, uid, gid: chowned.append((path, uid, gid))
    )
    utils.apply_owner(target, 7, 11)
    assert chowned == [(target, 7, 11)]


def test_apply_owner_skips_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-root process cannot chown, so the helper leaves the file alone;
    # the deployed run is root and applies the configured owner.
    target = tmp_path / "owned"
    target.write_text("x", encoding="utf-8")
    chowned: list[tuple[object, int, int]] = []
    monkeypatch.setattr(utils.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        utils.os, "chown", lambda path, uid, gid: chowned.append((path, uid, gid))
    )
    utils.apply_owner(target, 7, 11)
    assert chowned == []


def test_version_without_tag_prefix_strips_one_leading_v() -> None:
    # Both spellings of a release tag compare equal to the version an
    # installed tool prints: the one with the leading v and the one
    # without it. Everything else stays untouched, so a version that
    # merely starts with a v can never lose a character.
    assert version_without_tag_prefix("v3.7.0") == "3.7.0"
    assert version_without_tag_prefix("3.7.0") == "3.7.0"
    assert version_without_tag_prefix("vv1.0") == "v1.0"
    assert version_without_tag_prefix("") == ""


def test_the_architecture_query_comes_from_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Another architecture query in the [engine] table is exactly the argv
    # the helper runs, so the tool and its flags live in the config.
    engine = replace(
        make_config().engine,
        dpkg_architecture_command=("my-dpkg", "--arch"),
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append(list(command))
        return _FakeProc(0, "my-arch\n")

    monkeypatch.setattr(utils, "run_command", fake_run)
    assert utils.dpkg_architecture(engine, 30.0) == "my-arch"
    assert calls == [["my-dpkg", "--arch"]]


