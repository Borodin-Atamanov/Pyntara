"""Long-running Auto Port Forwarding service.

The service, started by the systemd unit auto_port_forwarding.service
deployed by the port_forwarding_setup task, keeps reverse ssh tunnels to
every port-forwarding server of the vault group. At system start it opens
the runtime secret vault through the shared vault opener, reads the
server addresses and the passphrase of the port-forwarding key, unlocks
the key in a dedicated ssh-agent and starts one supervisor thread per
server. Every thread keeps one ssh -R tunnel alive that forwards the
local SSH daemon port to a remote port on the server: the desired remote
port is derived deterministically from the hostname, and when the server
cannot grant it the thread asks for a random port and records the granted
one, so the port stays stable across reconnects. A dropped connection is
re-established after the geometric backoff, and every granted-port change
is saved to the state file and triggers a fresh System Metrics
collection, so the network report carries the current remote port of the
machine (docs/spec/port-forwarding-setup.md). A server whose address is
a local address of the machine itself, taken from ip -o addr, is
skipped, so the machine never tunnels onto itself. A vault without the
server group or the passphrase entry makes the service exit cleanly and
connect to nothing.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from pykeepass import PyKeePass

from pyntara import metrics
from pyntara.config import Config, load_config
from pyntara.logger import log_progress as _log
from pyntara.ssh import ssh_port_from_directives
from pyntara.utils import backoff_delay

# The server prints the granted random port to the client stderr; the
# pattern is the only source of the granted port, so the service reads it
# through the shared regex instead of guessing the port.
ALLOCATED_RE = re.compile(r"Allocated port (\d+) for remote forward")
# With -v the client prints a positive confirmation when the server
# accepted a fixed remote port, so a fixed-port forward is confirmed by
# its own success line instead of by waiting out a silence window.
SUCCESS_RE = re.compile(r"remote forward success for: listen (\d+)")
# A requested fixed port that is taken on the server makes ssh exit with
# this error line; the service then asks for a random port instead.
FAILED_RE = re.compile(r"remote port forwarding failed for listen port")


def desired_port(cfg: Config, hostname: str) -> int:
    """The deterministic desired remote port for a machine hostname.

    The port is a stable function of the hostname only: the same machine
    asks for the same port on every server, so the operator can predict
    the port in advance. sha256 of the hostname is mapped into the
    configured range; a collision on a busy server falls back to a
    random granted port.
    """

    pf = cfg.port_forwarding_setup
    value = int.from_bytes(hashlib.sha256(hostname.encode("utf-8")).digest()[:4], "big")
    span = pf.desired_port_max - pf.desired_port_min + 1
    return pf.desired_port_min + value % span


def read_server_addresses(kp: PyKeePass, group_title: str) -> list[str]:
    """The server addresses of the vault group, one per entry url.

    The address may be ipv4, ipv6 or a url; entries without an url are
    skipped, so a half-filled entry never breaks the whole group. A
    missing group yields an empty list, which makes the service connect
    to nothing.
    """

    group = kp.find_groups(name=group_title, first=True)
    if group is None:
        return []
    return [entry.url.strip() for entry in group.entries if entry.url and entry.url.strip()]


def _normalize_host(host: str) -> str:
    """The canonical form of a host for address comparison.

    The interface zone is not part of the address, so it is stripped
    first; an ipv6 or ipv4 address is normalized to its canonical
    compressed lowercase form, so any written variant of the same
    address compares equal. A bare hostname is returned lowercased,
    unchanged otherwise.
    """

    host = host.split("%", 1)[0]
    try:
        return str(ipaddress.IPv6Address(host))
    except ValueError:
        pass
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        pass
    return host.lower()


def own_addresses() -> set[str]:
    """The machine's own IP addresses from ip -o addr, or an empty set.

    The addresses come from the local interfaces, both families, so a
    server address that appears here is the machine itself. A failed or
    missing ip call yields an empty set, so the filter then keeps every
    server: that errs toward forwarding instead of dropping a real
    server.
    """

    try:
        result = subprocess.run(
            ["ip", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    own: set[str] = set()
    for line in result.stdout.splitlines():
        match = re.search(r"\binet6?\s+([0-9a-fA-F:.]+)", line)
        if match:
            own.add(_normalize_host(match.group(1)))
    return own


def _host_from_address(address: str) -> str:
    """The host of a server address, with a url scheme stripped.

    A url like https://vpn.example.com resolves to vpn.example.com; a
    bare ipv4, ipv6 or hostname is returned unchanged. The host is what
    the comparison with the machine's own addresses operates on.
    """

    if "://" in address:
        parsed = urlparse(address)
        if parsed.hostname:
            return parsed.hostname
    return address


def filter_own_servers(
    servers: list[str], own: set[str]
) -> tuple[list[str], list[str]]:
    """Split servers into connectable and own-machine ones.

    own carries the machine's own IP addresses from own_addresses; a
    server whose normalized address is in own is the machine itself and
    must not get a reverse tunnel, so it is returned in the skipped
    list. Every other server is returned in the kept list, forwarded as
    usual.
    """

    kept: list[str] = []
    skipped: list[str] = []
    for server in servers:
        if _normalize_host(_host_from_address(server)) in own:
            skipped.append(server)
        else:
            kept.append(server)
    return kept, skipped


def read_passphrase(kp: PyKeePass, entry_title: str) -> str | None:
    """The port-forwarding key passphrase from the vault entry, or None.

    The passphrase is read only in memory and never logged; a missing
    entry or an empty password both mean the key cannot be unlocked, and
    None is returned so the service connects to nothing.
    """

    entry = kp.find_entries(title=entry_title, first=True)
    if entry is None or not entry.password:
        return None
    password: str = entry.password
    return password


def _start_agent(passphrase: str, key_path: Path) -> dict[str, str] | None:
    """Start a dedicated ssh-agent and unlock the key; the agent env or None.

    The key is passphrase-protected, so it is loaded into a dedicated
    ssh-agent through SSH_ASKPASS_REQUIRE=force and a helper script that
    echoes the passphrase; after the load the helper is removed, and the
    long-running ssh processes sign through the agent without ever seeing
    the passphrase. A failed agent start or a failed unlock is logged and
    None is returned.
    """

    try:
        agent_out = subprocess.run(
            ["ssh-agent", "-s"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"cannot start the ssh-agent: {exc}")
        return None
    if agent_out.returncode != 0:
        _log(f"cannot start the ssh-agent: exited {agent_out.returncode}")
        return None
    env = dict(os.environ)
    for line in agent_out.stdout.splitlines():
        line = line.strip()
        if line.startswith("SSH_AUTH_SOCK="):
            env["SSH_AUTH_SOCK"] = line.split("=", 1)[1].split(";", 1)[0]
        elif line.startswith("SSH_AGENT_PID="):
            env["SSH_AGENT_PID"] = line.split("=", 1)[1].split(";", 1)[0]
    if "SSH_AUTH_SOCK" not in env:
        _log("cannot start the ssh-agent: no socket reported")
        return None
    helper_dir = Path(tempfile.mkdtemp(prefix="pyntara-pf-"))
    helper = helper_dir / "askpass.sh"
    helper.write_text('#!/bin/sh\necho "$PF_KEY_PASSPHRASE"\n', encoding="utf-8")
    helper.chmod(0o700)
    add_env = dict(env)
    add_env.update(
        {
            "SSH_ASKPASS": str(helper),
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": ":0",
            "PF_KEY_PASSPHRASE": passphrase,
        }
    )
    try:
        added = subprocess.run(
            ["ssh-add", str(key_path)],
            env=add_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"cannot unlock the port-forwarding key: {exc}")
        _remove_helper(helper_dir)
        _kill_agent(env)
        return None
    _remove_helper(helper_dir)
    if added.returncode != 0:
        _log(f"cannot unlock the port-forwarding key {key_path}")
        _kill_agent(env)
        return None
    return env


def _remove_helper(helper_dir: Path) -> None:
    """Remove the askpass helper directory after the key is loaded.

    The helper carried the passphrase for the unlock and is no longer
    needed once the key is in the agent; removing it keeps the passphrase
    from lingering on disk. The agent socket itself lives in its own
    directory and stays alive for the ssh processes.
    """

    try:
        for path in helper_dir.iterdir():
            try:
                path.unlink()
            except OSError:
                pass
        helper_dir.rmdir()
    except OSError:
        pass


def _kill_agent(env: dict[str, str]) -> None:
    """Kill the dedicated agent process, best effort.

    Called when the key unlock failed and the agent is useless, or when
    the service stops on its own; on a normal stop systemd kills the
    whole service control group, so the agent does not outlive the
    service either way.
    """

    agent_pid = env.get("SSH_AGENT_PID")
    if agent_pid:
        try:
            os.kill(int(agent_pid), 15)
        except (OSError, ValueError):
            pass


def _build_ssh_command(
    cfg: Config,
    key_path: Path,
    ssh_port: int,
    server: str,
    user: str,
    remote_port: str,
    local_port: int,
) -> list[str]:
    """The argv of one reverse-tunnel ssh process.

    The command is fully explicit: IdentitiesOnly and the explicit key
    path keep the client from offering the default passphrase-protected
    identities, BatchMode prevents any interactive prompt, accept-new
    records a first-seen server host key, and the keepalive options make
    a dead connection fail fast so the supervisor can reconnect. The -v
    flag is the positive forward confirmation source: ssh prints
    "remote forward success" once the server accepted the port, so the
    supervisor confirms a fixed-port forward by its success line instead
    of guessing.
    """

    pf = cfg.port_forwarding_setup
    return [
        "ssh",
        "-p",
        str(ssh_port),
        "-N",
        "-v",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ServerAliveInterval={pf.server_alive_interval_seconds}",
        "-o",
        f"ServerAliveCountMax={pf.server_alive_count_max}",
        "-o",
        f"ConnectTimeout={pf.connect_timeout_seconds}",
        "-i",
        str(key_path),
        "-R",
        f"{remote_port}:localhost:{local_port}",
        f"{user}@{server}",
    ]


def start_forward(
    env: dict[str, str],
    cfg: Config,
    key_path: Path,
    ssh_port: int,
    server: str,
    user: str,
    remote_port: str,
    local_port: int,
    timeout_seconds: int,
) -> tuple[subprocess.Popen[str], int | None, bool, str | None]:
    """Start one -R ssh process and read stderr until the outcome is known.

    Returns (process, granted, busy, error): granted is the allocated
    port when the server granted a random port (remote_port "0"); busy is
    True when the requested fixed port is taken on the server; error is
    None on success and a message otherwise. A fixed-port forward is
    confirmed by its own success line, so the caller knows the tunnel is
    up without waiting out a silence window; the reconnect loop repairs
    any later drop.
    """

    command = _build_ssh_command(
        cfg, key_path, ssh_port, server, user, remote_port, local_port
    )
    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )
    assert proc.stderr is not None
    os.set_blocking(proc.stderr.fileno(), False)
    deadline = time.monotonic() + timeout_seconds
    buffer = ""
    granted: int | None = None
    busy = False
    error: str | None = None

    def _scan() -> bool:
        """Scan the buffer for an outcome; True when the wait is settled."""

        nonlocal granted, busy, error
        match = ALLOCATED_RE.search(buffer)
        if match:
            granted = int(match.group(1))
            return True
        if SUCCESS_RE.search(buffer):
            return True
        if FAILED_RE.search(buffer):
            busy = True
            error = (
                buffer.strip().splitlines()[-1] if buffer.strip() else "port busy"
            )
            return True
        return False

    while time.monotonic() < deadline:
        if _scan():
            break
        if proc.poll() is not None:
            break
        try:
            chunk = proc.stderr.read()
        except (BlockingIOError, ValueError):
            chunk = ""
        if chunk:
            buffer += chunk
        else:
            time.sleep(0.2)
    # The process may have exited with output still buffered in the pipe;
    # drain it so a just-printed error or grant is not lost.
    while True:
        try:
            chunk = proc.stderr.read()
        except (BlockingIOError, ValueError):
            chunk = ""
        if not chunk:
            break
        buffer += chunk
    if not _scan() and proc.poll() is not None:
        error = f"ssh exited {proc.returncode}"
    return proc, granted, busy, error


def load_state(path: Path) -> dict[str, dict[str, int]]:
    """The recorded granted ports from the state file, or an empty dict.

    The state maps every server address to its per-local-port granted
    remote ports; a missing or unreadable file starts from an empty
    state, so a fresh service restart re-asks for the desired ports.
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    result: dict[str, dict[str, int]] = {}
    for server, ports in raw.items():
        if not isinstance(ports, dict):
            continue
        parsed: dict[str, int] = {}
        for local_port, remote_port in ports.items():
            if isinstance(remote_port, int) and not isinstance(remote_port, bool):
                parsed[str(local_port)] = remote_port
        result[str(server)] = parsed
    return result


def save_state(path: Path, state: dict[str, dict[str, int]]) -> None:
    """Persist the state atomically with root-only mode; errors are logged.

    The write goes through a temporary file in the same directory, so a
    crash never leaves a half-written state file behind.
    """

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.tmp")
        temp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except OSError as exc:
        _log(f"cannot save the port-forwarding state {path}: {exc}")


def trigger_collector(cfg: Config) -> None:
    """Trigger a fresh System Metrics collection after a port change.

    The assigned ports live in the state file read by the collector's
    port_forwarding module, so a port change only needs the collector to
    re-run: systemctl start --no-block returns immediately, the
    collector collects, commits and sends the network report on its own,
    and its non-blocking flock skips the trigger when a collection is
    already running. A failed trigger is logged; the next daily
    collection still carries the current ports.
    """

    collector_service = cfg.system_metrics_setup.collector.service_unit_name
    try:
        result = subprocess.run(
            ["systemctl", "start", "--no-block", collector_service],
            capture_output=True,
            text=True,
            timeout=cfg.port_forwarding_setup.connect_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(
            f"cannot trigger the metrics collector: {exc}",
            priority=cfg.port_forwarding_setup.error_priority,
        )
        return
    if result.returncode != 0:
        _log(
            f"cannot trigger the metrics collector: exited {result.returncode}",
            priority=cfg.port_forwarding_setup.error_priority,
        )
        return
    _log("metrics collector triggered for a fresh network report")


def run_forward_loop(
    cfg: Config,
    state: dict[str, dict[str, int]],
    lock: threading.Lock,
    server: str,
    ssh_port: int,
    local_port: int,
    key_path: Path,
    env: dict[str, str],
) -> None:
    """Keep one reverse tunnel to a server alive; run in one thread.

    The loop tries the recorded port first, then the deterministic
    desired port; when the requested port is taken on the server it asks
    for a random port and records the granted one. Every granted-port
    change is saved to the state file and reported through telemetry. A
    dropped connection is re-established after the geometric backoff; the
    escalation resets after a connection that stayed up for at least the
    maximum backoff, so a single drop after a long uptime waits only the
    base pause.
    """

    pf = cfg.port_forwarding_setup
    hostname = socket.gethostname()
    reconnect = 0
    while True:
        try:
            with lock:
                recorded = state.get(server, {}).get(str(local_port))
            remote_port = recorded if recorded is not None else desired_port(cfg, hostname)
            proc, granted, busy, error = start_forward(
                env,
                cfg,
                key_path,
                ssh_port,
                server,
                pf.remote_ssh_user,
                str(remote_port),
                local_port,
                pf.connect_timeout_seconds,
            )
            if busy:
                proc, granted, _, error = start_forward(
                    env,
                    cfg,
                    key_path,
                    ssh_port,
                    server,
                    pf.remote_ssh_user,
                    "0",
                    local_port,
                    pf.connect_timeout_seconds,
                )
                if granted is None:
                    _log(
                        f"{server}: no free remote port for local {local_port}: {error}",
                        priority=pf.error_priority,
                    )
                    reconnect += 1
                    time.sleep(
                        backoff_delay(
                            reconnect,
                            pf.backoff_base_seconds,
                            pf.backoff_multiplier,
                            pf.backoff_max_seconds,
                        )
                    )
                    continue
            elif error is not None:
                _log(f"{server}: cannot connect for local {local_port}: {error}")
                reconnect += 1
                time.sleep(
                    backoff_delay(
                        reconnect,
                        pf.backoff_base_seconds,
                        pf.backoff_multiplier,
                        pf.backoff_max_seconds,
                    )
                )
                continue
            port = granted if granted is not None else remote_port
            changed_port = False
            with lock:
                if state.get(server, {}).get(str(local_port)) != port:
                    state.setdefault(server, {})[str(local_port)] = port
                    save_state(pf.state_file_path, state)
                    changed_port = True
            if changed_port:
                trigger_collector(cfg)
            _log(f"{server}: forwarding local port {local_port} to remote port {port}")
            connected_at = time.monotonic()
            proc.wait()
            _log(f"{server}: connection to {port} dropped, reconnecting")
            if time.monotonic() - connected_at >= pf.backoff_max_seconds:
                reconnect = 0
            reconnect += 1
            time.sleep(
                backoff_delay(
                    reconnect,
                    pf.backoff_base_seconds,
                    pf.backoff_multiplier,
                    pf.backoff_max_seconds,
                )
            )
        except Exception as exc:  # noqa: BLE001 - the loop must never die silently
            _log(f"{server}: unexpected error: {exc}", priority=pf.error_priority)
            reconnect += 1
            time.sleep(
                backoff_delay(
                    reconnect,
                    pf.backoff_base_seconds,
                    pf.backoff_multiplier,
                    pf.backoff_max_seconds,
                )
            )


def main() -> None:
    """Run the port-forwarding loops until the service stops.

    The config path is the first command line argument; the systemd unit
    renders the configured system_config_path into the ExecStart line. A
    vault that cannot be opened, a missing key or a failed key unlock
    exit nonzero so systemd restarts the service; a vault that opens but
    carries no server group or no passphrase exits cleanly, because there
    is nothing to connect to.
    """

    if len(sys.argv) < 2:
        print("error: missing config path argument", file=sys.stderr)
        raise SystemExit(1)
    cfg = load_config(Path(sys.argv[1]))
    pf = cfg.port_forwarding_setup
    kp = metrics.open_runtime_vault(cfg)
    if kp is None:
        _log(
            "cannot open the runtime vault; the service will be restarted",
            priority=pf.error_priority,
        )
        raise SystemExit(1)
    servers = read_server_addresses(kp, pf.vault_group_title)
    own = own_addresses()
    servers, skipped = filter_own_servers(servers, own)
    if skipped:
        _log(f"skipping own server address(es): {', '.join(skipped)}")
    passphrase = read_passphrase(kp, pf.passphrase_entry_title)
    if not servers:
        _log(
            "no port-forwarding servers to connect to: the vault group is "
            "empty or lists only this machine"
        )
        return
    if not passphrase:
        _log(
            f"vault entry {pf.passphrase_entry_title!r} is absent, "
            "connecting to nothing",
            priority=pf.error_priority,
        )
        return
    ssh_port = ssh_port_from_directives(cfg.ssh_daemon_setup.directives)
    key_path = (
        cfg.ssh_daemon_setup.root_ssh_dir
        / cfg.ssh_daemon_setup.port_forwarding_private_key_file_name
    )
    if not key_path.is_file():
        _log(
            f"port-forwarding key missing: {key_path}; the service will be restarted",
            priority=pf.error_priority,
        )
        raise SystemExit(1)
    env = _start_agent(passphrase, key_path)
    if env is None:
        _log("cannot unlock the port-forwarding key", priority=pf.error_priority)
        raise SystemExit(1)
    state = load_state(pf.state_file_path)
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=run_forward_loop,
            args=(cfg, state, lock, server, ssh_port, ssh_port, key_path, env),
            daemon=True,
        )
        for server in servers
    ]
    _log(f"starting port-forwarding to {len(threads)} server(s)")
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
