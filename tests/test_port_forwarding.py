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
from pathlib import Path
from types import SimpleNamespace

import pytest
from pykeepass import PyKeePass, create_database
from support import make_config

import pyntara.port_forwarding as pf
from pyntara.port_forwarding import (
    desired_port,
    read_passphrase,
    read_server_addresses,
    run_forward_loop,
    start_forward,
)

VAULT_PASSWORD = "vault-secret"
FAKE_BIN = (
    "#!/usr/bin/env bash\n"
    "# Fake ssh for the tests: parse the -R argument and behave per env.\n"
    "# Every status message goes to stderr, exactly like the real ssh.\n"
    'R_SPEC=""\n'
    'prev=""\n'
    'for arg in "$@"; do\n'
    '  if [[ "$prev" == "-R" ]]; then R_SPEC="$arg"; fi\n'
    '  prev="$arg"\n'
    "done\n"
    'PORT="${R_SPEC%%:*}"\n'
    'if [[ "$PORT" == "0" ]]; then\n'
    '  if [[ -n "${FAKE_SSH_GRANT_FILE:-}" ]]; then\n'
    '    n=$(cat "$FAKE_SSH_GRANT_FILE")\n'
    '    echo "Allocated port $n for remote forward to localhost:30222" >&2\n'
    "    echo $((n + 1)) > \"$FAKE_SSH_GRANT_FILE\"\n"
    '  else\n'
    '    echo "Allocated port ${FAKE_SSH_GRANT_PORT:-45678} for remote forward to localhost:30222" >&2\n'
    "  fi\n"
    '  sleep "${FAKE_SSH_LIFETIME:-100}"\n'
    "  exit 0\n"
    "fi\n"
    'if [[ -n "${FAKE_SSH_BUSY:-}" ]]; then\n'
    '  echo "Error: remote port forwarding failed for listen port $PORT" >&2\n'
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


def _fake_commit_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create the fake commit command and its log path.

    The log path is embedded into the script, so the command needs no
    environment variable to record commits.
    """

    bindir = tmp_path / "commitbin"
    bindir.mkdir()
    log = tmp_path / "commit.log"
    script = (
        "#!/usr/bin/env bash\n"
        "# Fake commit_system_metrics: append the report to the log.\n"
        f'cat "$1" >> "{log}"\n'
        f'echo "REPORT_END" >> "{log}"\n'
    )
    _write_executable(bindir / "commit_system_metrics", script)
    return bindir, log


def _agent_env(bindir: Path, **extra: str) -> dict[str, str]:
    """An environment that resolves ssh through the fake bin directory."""

    env = {
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_SSH_LIFETIME": "1",
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
    kp.add_entry(
        group, title="Server 002", username="", password="", url="2001:db8::1"
    )
    kp.add_entry(
        group, title="Server 003", username="", password="", url="https://vpn.example.com"
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


def _count_reports(log: Path) -> int:
    """The number of committed reports in the fake commit log."""

    try:
        return log.read_text(encoding="utf-8").count("REPORT_END")
    except OSError:
        return 0


def _last_report(log: Path) -> dict[str, object]:
    """The last committed report JSON from the fake commit log."""

    blocks = log.read_text(encoding="utf-8").split("REPORT_END")
    nonempty = [b for b in blocks if b.strip()]
    return json.loads(nonempty[-1].strip())


class TestDesiredPort:
    def test_deterministic_and_in_range(self) -> None:
        config = make_config()
        first = desired_port(config, "dozor-gunid")
        second = desired_port(config, "dozor-gunid")
        assert first == second
        assert config.port_forwarding_setup.desired_port_min <= first
        assert first <= config.port_forwarding_setup.desired_port_max

    def test_differs_across_hostnames(self) -> None:
        config = make_config()
        ports = {
            desired_port(config, hostname) for hostname in ("aaa-babab", "bbb-babab")
        }
        assert len(ports) == 2


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
        assert read_passphrase(kp, "ssh_passphase_for_port_forwarding") == "the-passphrase"

    def test_read_passphrase_missing_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.kdbx"
        create_database(str(path), password=VAULT_PASSWORD)
        kp = PyKeePass(str(path), password=VAULT_PASSWORD)
        assert read_passphrase(kp, "ssh_passphase_for_port_forwarding") is None


class TestStartForward:
    @pytest.fixture(autouse=True)
    def _config(self, tmp_path: Path) -> None:
        self.config = make_config(
            port_forwarding_connect_timeout_seconds=1,
        )
        self.bindir = _fake_bin(tmp_path)
        self.key = tmp_path / "key"

    def test_grants_random_port(self) -> None:
        env = _agent_env(self.bindir, FAKE_SSH_GRANT_PORT="45678")
        proc, granted, busy, error = start_forward(
            env, self.config, self.key, 30222, "server", "i", "0", 30222, 5
        )
        assert granted == 45678
        assert busy is False
        assert error is None
        assert proc.poll() is None
        proc.terminate()
        proc.wait(timeout=5)

    def test_fixed_port_success(self) -> None:
        env = _agent_env(self.bindir)
        proc, granted, busy, error = start_forward(
            env, self.config, self.key, 30222, "server", "i", "41000", 30222, 5
        )
        assert granted is None
        assert busy is False
        assert error is None
        assert proc.poll() is None
        proc.terminate()
        proc.wait(timeout=5)

    def test_fixed_port_busy(self) -> None:
        env = _agent_env(self.bindir, FAKE_SSH_BUSY="1")
        proc, granted, busy, error = start_forward(
            env, self.config, self.key, 30222, "server", "i", "41000", 30222, 5
        )
        assert granted is None
        assert busy is True
        assert error is not None
        proc.wait(timeout=5)


class TestRunForwardLoop:
    @pytest.fixture(autouse=True)
    def _env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.tmp = tmp_path
        self.bindir = _fake_bin(tmp_path)
        commit_bin, self.commit_log = _fake_commit_dir(tmp_path)
        self.state_path = tmp_path / "state.json"
        self.key = tmp_path / "key"
        self.config = make_config(
            task_data_root=tmp_path,
            port_forwarding_connect_timeout_seconds=1,
            port_forwarding_state_file_path=self.state_path,
            system_metrics_command_path=commit_bin / "commit_system_metrics",
        )
        monkeypatch.setattr(pf.socket, "gethostname", lambda: "testhost")
        monkeypatch.setattr(pf.tempfile, "gettempdir", lambda: str(tmp_path))

        # Distinguish the reconnect pauses (>= 1s) from the stderr-watch
        # sleeps (0.2s): each pause is recorded and the second one stops
        # the loop, so every test observes one full reconnect cycle.
        pauses: list[int] = []
        self.pauses = pauses

        def fake_sleep(seconds: float) -> None:
            if seconds >= 1:
                pauses.append(int(seconds))
                if len(pauses) >= 2:
                    raise KeyboardInterrupt

        monkeypatch.setattr(pf.time, "sleep", fake_sleep)

    def _run(self, state: dict[str, dict[str, int]], env: dict[str, str]) -> None:
        lock = pf.threading.Lock()
        with pytest.raises(KeyboardInterrupt):
            run_forward_loop(
                self.config, state, lock, "server", 30222, 30222, self.key, env
            )

    def test_connects_records_and_commits(self) -> None:
        # A free desired port: the loop records it, saves the state and
        # commits one report, then waits the first backoff pause after
        # the fake connection drops.
        state: dict[str, dict[str, int]] = {}
        self._run(state, _agent_env(self.bindir))
        recorded = state["server"]["30222"]
        assert recorded == desired_port(self.config, "testhost")
        assert json.loads(self.state_path.read_text(encoding="utf-8"))["server"][
            "30222"
        ] == recorded
        assert _count_reports(self.commit_log) == 1
        report = _last_report(self.commit_log)
        assert report["hostname"] == "testhost"
        assert report["port_forwarding"] == [
            {"server": "server", "local_port": 30222, "remote_port": recorded}
        ]

    def test_busy_desired_port_falls_back_to_random(self) -> None:
        # A busy desired port makes the loop ask for a random port and
        # record the granted one instead.
        state: dict[str, dict[str, int]] = {}
        env = _agent_env(self.bindir, FAKE_SSH_BUSY="1", FAKE_SSH_GRANT_PORT="45678")
        self._run(state, env)
        assert state["server"]["30222"] == 45678
        assert _count_reports(self.commit_log) == 1
        assert _last_report(self.commit_log)["port_forwarding"][0]["remote_port"] == 45678

    def test_reconnect_with_new_port_recommits(self) -> None:
        # A reconnect that lands on a new random port updates the state
        # and commits a fresh report, so telemetry always carries the
        # current port.
        grant_file = self.tmp / "grant.txt"
        grant_file.write_text("10000\n", encoding="utf-8")
        env = _agent_env(
            self.bindir, FAKE_SSH_BUSY="1", FAKE_SSH_GRANT_FILE=str(grant_file)
        )
        state: dict[str, dict[str, int]] = {}
        self._run(state, env)
        assert state["server"]["30222"] == 10001
        assert _count_reports(self.commit_log) == 2
        reports = [
            json.loads(block.strip())
            for block in self.commit_log.read_text(encoding="utf-8").split("REPORT_END")
            if block.strip()
        ]
        ports = [r["port_forwarding"][0]["remote_port"] for r in reports]
        assert ports == [10000, 10001]

    def test_keeps_recorded_port_stable_across_reconnects(self) -> None:
        # A reconnect with a free recorded port keeps it, so the operator
        # address does not change; no extra report is committed.
        grant_file = self.tmp / "grant.txt"
        grant_file.write_text("20000\n", encoding="utf-8")
        env = _agent_env(self.bindir, FAKE_SSH_GRANT_FILE=str(grant_file))
        state: dict[str, dict[str, int]] = {"server": {"30222": 20000}}
        self._run(state, env)
        assert state["server"]["30222"] == 20000
        assert _count_reports(self.commit_log) == 0


class TestMain:
    @pytest.fixture(autouse=True)
    def _base(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.root_ssh = tmp_path / "root" / ".ssh"
        self.root_ssh.mkdir(parents=True)
        self.config = make_config(
            task_data_root=tmp_path,
            port_forwarding_connect_timeout_seconds=1,
            ssh_daemon_root_ssh_dir=self.root_ssh,
        )
        self.key = self.root_ssh / "id_ed25519_pf"
        self.key.write_text("dummy", encoding="utf-8")
        monkeypatch.setattr(pf, "load_config", lambda path: self.config)
        monkeypatch.setattr(pf.socket, "gethostname", lambda: "testhost")
        monkeypatch.setattr(
            "sys.argv", ["pyntara.port_forwarding", str(tmp_path / "config.toml")]
        )

    def _kp(self, *, group: bool, passphrase: bool) -> SimpleNamespace:
        entries = []
        if passphrase:
            entries.append(
                SimpleNamespace(password="the-passphrase")
            )
        group_entries = [SimpleNamespace(url="169.58.51.98")]
        return SimpleNamespace(
            find_groups=lambda name, first: (
                SimpleNamespace(entries=group_entries) if group else None
            ),
            find_entries=lambda title, first: (
                entries[0] if passphrase else None
            ),
        )

    def test_exits_cleanly_without_servers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A vault without the server group means nothing to connect to;
        # the service exits cleanly instead of failing.
        monkeypatch.setattr(
            pf.metrics, "open_runtime_vault", lambda cfg: self._kp(group=False, passphrase=True)
        )
        assert pf.main() is None

    def test_exits_cleanly_without_passphrase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A vault without the passphrase entry cannot unlock the key, so
        # the service exits cleanly.
        monkeypatch.setattr(
            pf.metrics, "open_runtime_vault", lambda cfg: self._kp(group=True, passphrase=False)
        )
        assert pf.main() is None

    def test_exits_nonzero_without_vault(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An unopenable vault is recoverable, so the service exits nonzero
        # for systemd to restart it.
        monkeypatch.setattr(pf.metrics, "open_runtime_vault", lambda cfg: None)
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
            pf.metrics, "open_runtime_vault",
            lambda cfg: self._kp(group=True, passphrase=True),
        )
        monkeypatch.setattr(pf, "_start_agent", lambda passphrase, key: {"PATH": "/bin"})
        monkeypatch.setattr(pf.threading, "Thread", FakeThread)
        assert pf.main() is None
        assert len(created) == 1
        target, args = created[0]
        assert target is run_forward_loop
        assert args[3] == "169.58.51.98"
