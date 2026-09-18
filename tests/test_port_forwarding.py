"""Unit tests for the Auto Port Forwarding service module.

The service is exercised with a fake ssh executable and a fake
commit_system_metrics command, so the port parsing, the reconnect loop,
the state file and the telemetry commits are asserted without real time,
network or vaults (vault reads use real cheap-KDF test databases). The
journal is disabled by conftest.
"""

from __future__ import annotations

import json
import os
import stat
from itertools import islice
from pathlib import Path
from types import SimpleNamespace

import pytest
from pykeepass import PyKeePass, create_database
from support import FakeProc

import pyntara.port_forwarding as pf
from pyntara.forwarding_ports import candidate_ports, desired_port
from pyntara.port_forwarding import (
    _normalize_host,
    _open_tunnel,
    filter_own_servers,
    own_addresses,
    read_passphrase,
    read_server_addresses,
    run_forward_loop,
    save_state,
    start_forward,
)
from pyntara.values import port_forwarding_setup as values
from pyntara.values import ssh_daemon_setup as ssh_daemon_values
from pyntara.values.ssh_daemon_setup import SshDirective

VAULT_PASSWORD = "vault-secret"
FAKE_BIN = (
    "#!/usr/bin/env bash\n"
    "# Fake ssh for the tests: read the -R argument and answer per script.\n"
    "# Every status message goes to stderr, exactly like the real ssh.\n"
    'R_SPEC=""\n'
    'prev=""\n'
    'for arg in "$@"; do\n'
    '  if [[ "$prev" == "-R" ]]; then R_SPEC="$arg"; fi\n'
    '  prev="$arg"\n'
    "done\n"
    'PORT="${R_SPEC%%:*}"\n'
    'if [[ -n "${FAKE_SSH_ARGV_LOG:-}" ]]; then echo "$R_SPEC" >> "$FAKE_SSH_ARGV_LOG"; fi\n'
    "# The busy script holds one line per attempt: the ports the server\n"
    "# refuses for that attempt, space separated. A consumed line is\n"
    "# dropped, and the last line repeats, so a script with one line\n"
    "# describes a port that stays taken.\n"
    'BUSY=""\n'
    'if [[ -n "${FAKE_SSH_BUSY_SCRIPT:-}" && -r "${FAKE_SSH_BUSY_SCRIPT}" ]]; then\n'
    '  BUSY="$(head -n 1 "$FAKE_SSH_BUSY_SCRIPT")"\n'
    '  REST="$(tail -n +2 "$FAKE_SSH_BUSY_SCRIPT")"\n'
    '  if [[ -n "$REST" ]]; then printf "%s\\n" "$REST" > "$FAKE_SSH_BUSY_SCRIPT"; fi\n'
    "fi\n"
    'if [[ " $BUSY " == *" $PORT "* ]]; then\n'
    '  echo "Error: remote port forwarding failed for listen port $PORT" >&2\n'
    "  exit 255\n"
    "fi\n"
    'if [[ -n "${FAKE_SSH_FAIL_CONNECT:-}" ]]; then\n'
    '  echo "ssh: connect to host server port 30222: Connection refused" >&2\n'
    "  exit 255\n"
    "fi\n"
    'echo "remote forward success for: listen $PORT, connect localhost:30222" >&2\n'
    'sleep "${FAKE_SSH_LIFETIME:-100}"\n'
    "exit 0\n"
)


def _write_executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_bin(tmp_path: Path) -> Path:
    """Create a bin directory with the fake ssh on PATH."""

    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_executable(bindir / "ssh", FAKE_BIN)
    return bindir


def _agent_env(bindir: Path, lifetime: float = 1, **extra: str) -> dict[str, str]:
    """An environment that resolves ssh through the fake bin directory.

    lifetime is how long the fake ssh keeps the tunnel open. A test that
    only reads the parsed outcome keeps the default, because it asserts
    that the returned process is still running. The reconnect loop waits
    for the child to exit, and its pacing comes from the patched
    time.sleep of that test class, so a long real lifetime there only
    delays the drop the test is waiting for.
    """

    env = {
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_SSH_LIFETIME": str(lifetime),
    }
    env.update(extra)
    return env


def _make_vault(tmp_path: Path) -> PyKeePass:
    """A real cheap-KDF vault with the port-forwarding group and passphrase."""

    path = tmp_path / "vault.kdbx"
    create_database(str(path), password=VAULT_PASSWORD)
    kp = PyKeePass(str(path), password=VAULT_PASSWORD)
    group = kp.add_group(kp.root_group, "port_forwarding_servers")
    kp.add_entry(
        group, title="Server 001", username="", password="", url="169.58.51.98"
    )
    kp.add_entry(group, title="Server 002", username="", password="", url="2001:db8::1")
    kp.add_entry(
        group,
        title="Server 003",
        username="",
        password="",
        url="https://vpn.example.com",
    )
    kp.add_entry(group, title="Broken", username="", password="", url="")
    kp.add_entry(
        kp.root_group,
        title="ssh_passphase_for_port_forwarding",
        username="",
        password="the-passphrase",
    )
    kp.save()
    return kp


class TestOwnServers:
    def test_own_addresses_parses_both_families(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdout = (
            "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
            "1: lo    inet6 ::1/128 scope host \\       valid_lft forever\n"
            "2: eth0    inet 192.168.1.5/24 brd 192.168.1.255 scope global "
            "dynamic eth0\\       valid_lft 86399sec preferred_lft 86399sec\n"
            "3: wlan0    inet6 fe80::1234:abcd/64 scope link \\       valid_lft "
            "forever\n"
        )
        monkeypatch.setattr(
            pf.subprocess, "run", lambda *args, **kwargs: FakeProc(0, stdout)
        )
        assert own_addresses() == {
            "127.0.0.1",
            "::1",
            "192.168.1.5",
            "fe80::1234:abcd",
        }

    def test_own_addresses_timeout_comes_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The bound of the ip call is a declared value, so a slow machine
        # is answered in the values and not in the code.
        seen: list[float] = []

        def _run(*args: object, **kwargs: object) -> FakeProc:
            seen.append(float(kwargs["timeout"]))  # type: ignore[arg-type]
            return FakeProc(0, "")

        monkeypatch.setattr(pf.subprocess, "run", _run)
        own_addresses()
        assert seen == [float(values.OWN_ADDRESSES_TIMEOUT_SECONDS)]

    def test_own_addresses_failure_keeps_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failed ip call must not drop any server: the empty set errs
        # toward forwarding, never toward skipping a real server.
        monkeypatch.setattr(
            pf.subprocess, "run", lambda *args, **kwargs: FakeProc(1, "")
        )
        assert own_addresses() == set()

    def test_filter_own_servers_splits_by_matching_address(self) -> None:
        own = {"127.0.0.1", "192.168.1.5", "fe80::1"}
        servers = [
            "192.168.1.5",
            "169.58.51.98",
            "2001:db8::1",
            "https://192.168.1.5",
            "https://vpn.example.com",
        ]
        kept, skipped = filter_own_servers(servers, own)
        assert kept == ["169.58.51.98", "2001:db8::1", "https://vpn.example.com"]
        assert skipped == ["192.168.1.5", "https://192.168.1.5"]

    def test_filter_own_servers_empty_own_keeps_everything(self) -> None:
        servers = ["169.58.51.98", "https://vpn.example.com"]
        kept, skipped = filter_own_servers(servers, set())
        assert kept == servers
        assert skipped == []

    def test_filter_own_servers_matches_any_written_variant(self) -> None:
        own = {"205:6f71:2cee:2d23:615e:8f2b:bc79:4fdb"}
        servers = [
            "205:6f71:2cee:2d23:615e:8f2b:bc79:4fdb",
            "0205:6F71:2CEE:2D23:615E:8F2B:BC79:4FDB",
            "205:6f71:2cee:2d23:615e:8f2b:bc79:4fdb%eth0",
            "169.58.51.98",
        ]
        kept, skipped = filter_own_servers(servers, own)
        assert kept == ["169.58.51.98"]
        assert skipped == servers[:-1]

    def test_normalize_host(self) -> None:
        assert _normalize_host("0205:6F71:2CEE:2D23:615E:8F2B:BC79:4FDB") == (
            "205:6f71:2cee:2d23:615e:8f2b:bc79:4fdb"
        )
        assert _normalize_host("2001:0DB8:0:0:0:0:0:1") == "2001:db8::1"
        assert _normalize_host("FE80::1%eth0") == "fe80::1"
        assert _normalize_host("192.168.1.5") == "192.168.1.5"
        assert _normalize_host("vpn.example.com") == "vpn.example.com"


class TestVaultReads:
    def test_read_server_addresses(self, tmp_path: Path) -> None:
        kp = _make_vault(tmp_path)
        addresses = read_server_addresses(kp, "port_forwarding_servers")
        assert addresses == ["169.58.51.98", "2001:db8::1", "https://vpn.example.com"]

    def test_read_server_addresses_missing_group(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.kdbx"
        create_database(str(path), password=VAULT_PASSWORD)
        kp = PyKeePass(str(path), password=VAULT_PASSWORD)
        assert read_server_addresses(kp, "port_forwarding_servers") == []

    def test_read_passphrase(self, tmp_path: Path) -> None:
        kp = _make_vault(tmp_path)
        assert (
            read_passphrase(kp, "ssh_passphase_for_port_forwarding") == "the-passphrase"
        )

    def test_read_passphrase_missing_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.kdbx"
        create_database(str(path), password=VAULT_PASSWORD)
        kp = PyKeePass(str(path), password=VAULT_PASSWORD)
        assert read_passphrase(kp, "ssh_passphase_for_port_forwarding") is None


class TestStartForward:
    @pytest.fixture(autouse=True)
    def _config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(values, "CONNECT_TIMEOUT_SECONDS", 1)
        self.tmp = tmp_path
        self.bindir = _fake_bin(tmp_path)
        self.key = tmp_path / "key"
        # The pause between the stderr polls is pacing, not behaviour:
        # the fake ssh answers within milliseconds, so the real 0.2s
        # pause only slows the test down.
        monkeypatch.setattr(pf.time, "sleep", lambda _seconds: None)

    def test_accepts_the_requested_port(self) -> None:
        env = _agent_env(self.bindir)
        proc, busy, error = start_forward(
            env, self.key, 30222, "server", "i", 41000, 30222, 5
        )
        assert busy is False
        assert error is None
        assert proc.poll() is None
        proc.terminate()
        proc.wait(timeout=5)

    def test_reports_a_taken_port_as_busy(self) -> None:
        # The server refuses the port, so the caller walks on: busy is the
        # reason, and the error text carries the line of the server.
        script = self.tmp / "busy.txt"
        script.write_text("41000\n", encoding="utf-8")
        env = _agent_env(self.bindir, FAKE_SSH_BUSY_SCRIPT=str(script))
        proc, busy, error = start_forward(
            env, self.key, 30222, "server", "i", 41000, 30222, 5
        )
        assert busy is True
        assert error is not None
        assert "failed for listen port" in error
        proc.wait(timeout=5)

    def test_reports_a_connection_failure_as_an_error(self) -> None:
        # A port that cannot be reached at all is not a busy port: busy
        # stays False and the message names the failure, so the caller
        # backs off instead of walking the whole chain.
        env = _agent_env(self.bindir, FAKE_SSH_FAIL_CONNECT="1")
        proc, busy, error = start_forward(
            env, self.key, 30222, "server", "i", 41000, 30222, 5
        )
        assert busy is False
        assert error is not None
        assert "Connection refused" in error
        proc.wait(timeout=5)


class TestBuildSshCommand:
    def test_reads_the_configured_template(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The whole ssh call, arguments and options alike, comes from the
        # declared values, so a machine that needs another option changes
        # the values and never the module.
        configured = (
            "ssh",
            "-p",
            "{ssh_port}",
            "-N",
            "-o",
            "ExitOnForwardFailure=yes",
            "-i",
            "{key_path}",
            "-R",
            "{remote_port}:{remote_bind_address}:{local_port}",
            "{user}@{host}",
            "-o",
            "ExtraOption=yes",
        )
        monkeypatch.setattr(values, "SSH_FORWARD_COMMAND", configured)
        monkeypatch.setattr(values, "REMOTE_BIND_ADDRESS", "127.0.0.1")
        command = pf._build_ssh_command(
            Path("/key"), 30222, "server", "i", "41000", 30222
        )
        assert command[-2:] == ["-o", "ExtraOption=yes"]
        assert "41000:127.0.0.1:30222" in command
        assert "i@server" in command
        assert command[command.index("-p") + 1] == "30222"
        assert command[command.index("-i") + 1] == "/key"

    def test_keepalive_and_connect_bounds_come_from_the_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The keepalive and connect values fill the placeholders of the
        # ssh command, so they keep one home.
        monkeypatch.setattr(values, "SERVER_ALIVE_INTERVAL_SECONDS", 77)
        monkeypatch.setattr(values, "SERVER_ALIVE_COUNT_MAX", 5)
        monkeypatch.setattr(values, "CONNECT_TIMEOUT_SECONDS", 9)
        command = pf._build_ssh_command(
            Path("/key"), 30222, "server", "i", "41000", 30222
        )
        assert "ServerAliveInterval=77" in command
        assert "ServerAliveCountMax=5" in command
        assert "ConnectTimeout=9" in command


class TestStartAgent:
    def test_env_names_helper_and_command_come_from_the_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The unlock sets the declared variables, writes the declared
        # helper and runs the declared ssh-add call, so the passphrase
        # never travels through a literal of the module.
        calls: list[list[str]] = []
        captured: dict[str, str] = {}

        def fake_run(command: list[str], **kwargs: object) -> FakeProc:
            calls.append(list(command))
            if command[0] == "ssh-agent":
                return FakeProc(
                    0,
                    f"{values.AGENT_SOCKET_ENV_KEY}=/tmp/sock; export x;\n"
                    f"{values.AGENT_PID_ENV_KEY}=4242; export x;\n",
                )
            environment = kwargs.get("env")
            if isinstance(environment, dict):
                for name, value in environment.items():
                    captured[str(name)] = str(value)
            return FakeProc(0, "")

        monkeypatch.setattr(pf.subprocess, "run", fake_run)
        key = tmp_path / "key"
        key.write_text("key", encoding="utf-8")
        env = pf._start_agent("passphrase", key)
        assert env is not None
        assert calls[0] == list(values.AGENT_START_COMMAND)
        assert calls[-1] == [
            part.format(key_path=str(key)) for part in values.KEY_ADD_COMMAND
        ]
        assert captured[values.DISPLAY_ENV_KEY] == values.ASKPASS_DISPLAY
        assert captured[values.PASSPHRASE_ENV_KEY] == "passphrase"
        askpass_variable = next(iter(values.ASKPASS_ENV))
        assert captured[askpass_variable].endswith(values.ASKPASS_HELPER_FILE_NAME)


def test_state_write_uses_the_declared_suffix_and_indent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The temporary file of the atomic write and the indentation of the
    # JSON are declared values, so the state layout is readable in one
    # place.
    monkeypatch.setattr(values, "STATE_FILE_PATH", tmp_path / "state.json")
    state = {"server": {"30222": 20000}}
    save_state(state)
    target = tmp_path / "state.json"
    assert target.read_text(encoding="utf-8") == json.dumps(
        state, ensure_ascii=False, indent=values.STATE_JSON_INDENT
    )
    assert not (tmp_path / f"state.json{values.STATE_TEMP_FILE_SUFFIX}").exists()


def test_a_failed_state_write_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The atomic write cleans up after itself: when the move into place
    # fails, the temporary file is gone and the state file that was there
    # before still carries the old state, so the state directory holds no
    # stray file.
    monkeypatch.setattr(values, "STATE_FILE_PATH", tmp_path / "state.json")
    target = tmp_path / "state.json"
    save_state({"server": {"30222": 20000}})
    before = target.read_text(encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(pf.os, "replace", fail_replace)
    save_state({"server": {"30222": 20001}})
    assert target.read_text(encoding="utf-8") == before
    assert list(tmp_path.iterdir()) == [target]
    assert not (tmp_path / f"state.json{values.STATE_TEMP_FILE_SUFFIX}").exists()


class TestOpenTunnel:
    """Tests for the walk over the candidate chain of the machine."""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.bindir = _fake_bin(tmp_path)
        self.key = tmp_path / "key"
        self.argv_log = tmp_path / "argv.txt"
        monkeypatch.setattr(values, "CONNECT_TIMEOUT_SECONDS", 1)
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(pf.socket, "gethostname", lambda: "testhost")
        # The pause between two attempts is the behaviour under test, so
        # every pause of the loop is recorded; the 0.2s stderr poll is
        # pacing only and is dropped, so the tests stay fast.
        self.pauses: list[float] = []

        def fake_sleep(seconds: float) -> None:
            if seconds >= 1:
                self.pauses.append(seconds)

        monkeypatch.setattr(pf.time, "sleep", fake_sleep)

    def _agent_env(
        self, busy_script: Path | None = None, **extra: str
    ) -> dict[str, str]:
        if busy_script is not None:
            extra["FAKE_SSH_BUSY_SCRIPT"] = str(busy_script)
        return _agent_env(
            self.bindir,
            lifetime=0.05,
            FAKE_SSH_ARGV_LOG=str(self.argv_log),
            **extra,
        )

    def _attempts(self) -> list[str]:
        """The -R argument of every attempt, in the order they ran."""

        if not self.argv_log.exists():
            return []
        return self.argv_log.read_text(encoding="utf-8").split()

    def _busy_script(self, *lines: object) -> Path:
        """A busy script with one line per attempt, in the given order."""

        script = self.tmp / "busy.txt"
        script.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return script

    def _second_candidate(self) -> int:
        """The candidate the walk asks for after the first one."""

        return list(islice(candidate_ports("testhost"), 2))[1]

    def _tiny_range(self) -> None:
        """Point the declared range at exactly three ports."""

        self.monkeypatch.setattr(values, "DESIRED_PORT_MIN", 1000)
        self.monkeypatch.setattr(values, "DESIRED_PORT_MAX", 1002)

    def _open(self, env: dict[str, str]):
        return _open_tunnel(
            "server",
            30222,
            30222,
            self.key,
            env,
        )

    def test_the_first_candidate_is_accepted_when_it_is_free(self) -> None:
        # Nothing is taken on the server, so the machine gets its
        # predictable number on the first try, without a pause.
        first = desired_port("testhost")
        opened = self._open(self._agent_env())
        assert opened is not None
        proc, port = opened
        assert port == first
        assert self._attempts() == [f"{first}:localhost:30222"]
        assert self.pauses == []
        proc.terminate()
        proc.wait(timeout=5)

    def test_a_taken_candidate_moves_to_the_next_one(self) -> None:
        # The server refuses the first candidate, so the walk asks for the
        # second one after the configured base pause. Every request is a
        # candidate of the machine: no port is ever asked from the server.
        first = desired_port("testhost")
        second = self._second_candidate()
        env = self._agent_env(self._busy_script(first))
        opened = self._open(env)
        assert opened is not None
        proc, port = opened
        assert port == second
        assert self._attempts() == [
            f"{first}:localhost:30222",
            f"{second}:localhost:30222",
        ]
        assert self.pauses == [float(values.BACKOFF_BASE_SECONDS)]
        proc.terminate()
        proc.wait(timeout=5)

    def test_a_failed_connection_ends_the_walk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A port that cannot be reached at all is not a busy port, so the
        # walk stops instead of asking every candidate of the range.
        messages: list[str] = []
        monkeypatch.setattr(
            pf, "_log", lambda message, **kwargs: messages.append(str(message))
        )
        opened = self._open(self._agent_env(FAKE_SSH_FAIL_CONNECT="1"))
        assert opened is None
        assert len(self._attempts()) == 1
        assert any("cannot connect" in message for message in messages)

    def test_a_full_range_of_taken_ports_ends_the_walk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The walk is bounded by the range itself: with a three port range
        # every port of it is offered once, in the order of the chain, and
        # then the walk stops, because the next candidate could only
        # repeat one of them.
        messages: list[str] = []
        monkeypatch.setattr(
            pf, "_log", lambda message, **kwargs: messages.append(str(message))
        )
        self._tiny_range()
        env = self._agent_env(self._busy_script("1000 1001 1002"))
        opened = self._open(env)
        assert opened is None
        attempted = sorted(
            int(attempt.split(":", 1)[0]) for attempt in self._attempts()
        )
        assert attempted == [1000, 1001, 1002]
        assert len(self.pauses) == len(attempted)
        assert any("every port of the range" in message for message in messages)


class TestRunForwardLoop:
    @pytest.fixture(autouse=True)
    def _env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.bindir = _fake_bin(tmp_path)
        self.state_path = tmp_path / "state.json"
        self.key = tmp_path / "key"
        self.argv_log = tmp_path / "argv.txt"
        monkeypatch.setattr(values, "CONNECT_TIMEOUT_SECONDS", 1)
        monkeypatch.setattr(values, "STATE_FILE_PATH", self.state_path)
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(pf.socket, "gethostname", lambda: "testhost")

        # A port change triggers the metrics collector instead of sending
        # a separate report; the trigger is recorded by a fake.
        self.triggers: list[bool] = []
        monkeypatch.setattr(
            pf, "trigger_collection", lambda: self.triggers.append(True)
        )

        # Distinguish the pauses of the loop (>= 1s) from the stderr-watch
        # sleeps (0.2s): each pause is recorded, and the pause that
        # reaches stop_after stops the loop, so every test observes whole
        # connection cycles.
        self.pauses: list[int] = []
        self.stop_after = 2

        def fake_sleep(seconds: float) -> None:
            if seconds >= 1:
                self.pauses.append(int(seconds))
                if len(self.pauses) >= self.stop_after:
                    raise KeyboardInterrupt

        monkeypatch.setattr(pf.time, "sleep", fake_sleep)

    def _agent_env(
        self, busy_script: Path | None = None, **extra: str
    ) -> dict[str, str]:
        """Env for the reconnect loop: the fake ssh drops the tunnel at once."""

        if busy_script is not None:
            extra["FAKE_SSH_BUSY_SCRIPT"] = str(busy_script)
        return _agent_env(
            self.bindir,
            lifetime=0.05,
            FAKE_SSH_ARGV_LOG=str(self.argv_log),
            **extra,
        )

    def _attempts(self) -> list[str]:
        if not self.argv_log.exists():
            return []
        return self.argv_log.read_text(encoding="utf-8").split()

    def _busy_script(self, *lines: object) -> Path:
        script = self.tmp / "busy.txt"
        script.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return script

    def _second_candidate(self) -> int:
        return list(islice(candidate_ports("testhost"), 2))[1]

    def _run(self, state: dict[str, dict[str, int]], env: dict[str, str]) -> None:
        lock = pf.threading.Lock()
        with pytest.raises(KeyboardInterrupt):
            run_forward_loop(
                state, lock, "server", 30222, 30222, self.key, env
            )

    def test_state_file_carries_the_declared_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The mode and the location of the state file are declared
        # values, so no literal of the write can slip in.
        monkeypatch.setattr(values, "STATE_FILE_PATH", tmp_path / "state.json")
        save_state({"server": {"30222": 20000}})
        target = values.STATE_FILE_PATH
        assert stat.S_IMODE(target.stat().st_mode) == values.STATE_FILE_MODE

    def test_connects_records_and_triggers_collector(self) -> None:
        # A free first candidate: the loop records it, saves the state and
        # triggers one collector run, then waits the backoff pause after
        # the fake connection drops.
        state: dict[str, dict[str, int]] = {}
        self._run(state, self._agent_env())
        recorded = state["server"]["30222"]
        assert recorded == desired_port("testhost")
        assert (
            json.loads(self.state_path.read_text(encoding="utf-8"))["server"]["30222"]
            == recorded
        )
        assert len(self.triggers) == 1

    def test_the_port_returns_to_the_first_candidate_on_reconnect(self) -> None:
        # Every walk starts at the first candidate, so a reconnect keeps
        # the machine on its predictable number and sends no fresh report.
        state: dict[str, dict[str, int]] = {}
        self._run(state, self._agent_env())
        first = desired_port("testhost")
        assert state["server"]["30222"] == first
        assert len(self.triggers) == 1
        attempts = self._attempts()
        assert len(attempts) >= 2
        assert set(attempts) == {f"{first}:localhost:30222"}

    def test_a_taken_candidate_moves_the_machine_to_the_next_one(self) -> None:
        # The server takes the first candidate, so the machine moves to
        # the second one: the port stays deterministic and no port is
        # asked from the server.
        first = desired_port("testhost")
        second = self._second_candidate()
        state: dict[str, dict[str, int]] = {}
        self._run(state, self._agent_env(self._busy_script(first)))
        assert state["server"]["30222"] == second
        assert self._attempts() == [
            f"{first}:localhost:30222",
            f"{second}:localhost:30222",
        ]

    def test_a_change_of_port_triggers_a_fresh_report(self) -> None:
        # The session that just died keeps the port on the server, so the
        # walk after the drop moves to the next candidate; the state and
        # the report follow the machine.
        first = desired_port("testhost")
        second = self._second_candidate()
        self.stop_after = 3
        state: dict[str, dict[str, int]] = {}
        self._run(state, self._agent_env(self._busy_script("", first, "")))
        assert state["server"]["30222"] == second
        assert len(self.triggers) == 2

    def test_a_taken_candidate_is_named_in_the_journal(self) -> None:
        # The journal says which port was taken and that the walk moved on,
        # so a machine whose number changed explains itself.
        first = desired_port("testhost")
        messages: list[str] = []
        self.monkeypatch.setattr(
            pf, "_log", lambda message, **kwargs: messages.append(str(message))
        )
        state: dict[str, dict[str, int]] = {}
        self._run(state, self._agent_env(self._busy_script(first)))
        assert (
            f"server: remote port {first} is taken there, "
            "trying the next candidate" in messages
        )


class TestMain:
    @pytest.fixture(autouse=True)
    def _base(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root_ssh = tmp_path / "root" / ".ssh"
        self.root_ssh.mkdir(parents=True)
        monkeypatch.setattr(values, "CONNECT_TIMEOUT_SECONDS", 1)
        monkeypatch.setattr(ssh_daemon_values, "ROOT_SSH_DIR", self.root_ssh)
        monkeypatch.setattr(
            ssh_daemon_values, "DIRECTIVES", (SshDirective("Port", "30222"),)
        )
        self.key = self.root_ssh / "id_ed25519_pf"
        self.key.write_text("dummy", encoding="utf-8")
        monkeypatch.setattr(pf.socket, "gethostname", lambda: "testhost")

    def _kp(self, *, group: bool, passphrase: bool) -> SimpleNamespace:
        entries = []
        if passphrase:
            entries.append(SimpleNamespace(password="the-passphrase"))
        group_entries = [SimpleNamespace(url="169.58.51.98")]
        return SimpleNamespace(
            find_groups=lambda name, first: (
                SimpleNamespace(entries=group_entries) if group else None
            ),
            find_entries=lambda title, first: entries[0] if passphrase else None,
        )

    def test_journals_under_the_configured_service_identifier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The auto forwarding service announces itself under its own
        # configured identifier, never under the engine name, so a journal
        # query separates the service from the run that deployed it.
        configured: list[str] = []
        monkeypatch.setattr(pf, "configure_journal", configured.append)
        monkeypatch.setattr(
            pf.metrics,
            "open_runtime_vault",
            lambda: self._kp(group=False, passphrase=True),
        )
        pf.main()
        expected = values.JOURNAL_IDENTIFIER
        assert configured[-1] == expected

    def test_exits_cleanly_without_servers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A vault without the server group means nothing to connect to;
        # the service exits cleanly instead of failing.
        monkeypatch.setattr(
            pf.metrics,
            "open_runtime_vault",
            lambda: self._kp(group=False, passphrase=True),
        )
        pf.main()

    def test_exits_cleanly_without_passphrase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A vault without the passphrase entry cannot unlock the key, so
        # the service exits cleanly.
        monkeypatch.setattr(
            pf.metrics,
            "open_runtime_vault",
            lambda: self._kp(group=True, passphrase=False),
        )
        pf.main()

    def test_exits_nonzero_without_vault(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An unopenable vault is recoverable, so the service exits nonzero
        # for systemd to restart it.
        monkeypatch.setattr(pf.metrics, "open_runtime_vault", lambda: None)
        with pytest.raises(SystemExit) as exc:
            pf.main()
        assert exc.value.code == 1

    def test_starts_one_loop_per_server(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The service starts one supervisor thread per server and waits
        # for them; the threads are recorded by a fake to keep the test
        # deterministic.
        created: list[tuple[object, tuple[object, ...]]] = []

        class FakeThread:
            def __init__(
                self, *, target: object, args: tuple[object, ...], daemon: bool
            ) -> None:
                created.append((target, args))
                self.daemon = daemon

            def start(self) -> None:
                return None

            def join(self) -> None:
                return None

        monkeypatch.setattr(
            pf.metrics,
            "open_runtime_vault",
            lambda: self._kp(group=True, passphrase=True),
        )
        monkeypatch.setattr(
            pf, "_start_agent", lambda *args, **kwargs: {"PATH": "/bin"}
        )
        monkeypatch.setattr(pf.threading, "Thread", FakeThread)
        pf.main()
        assert len(created) == 1
        target, args = created[0]
        assert target is run_forward_loop
        assert args[2] == "169.58.51.98"
