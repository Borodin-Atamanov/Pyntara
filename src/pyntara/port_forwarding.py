"""Long-running Auto Port Forwarding service.

The service, started by the systemd unit auto_port_forwarding.service
deployed by the port_forwarding_setup task, keeps reverse ssh tunnels to
every port-forwarding server of the vault group. At system start it opens
the runtime secret vault through the shared vault opener, reads the
server addresses and the passphrase of the port-forwarding key, unlocks
the key in a dedicated ssh-agent and starts one supervisor thread per
server. Every thread keeps one ssh -R tunnel alive that forwards the
local SSH daemon port to a remote port on the server: the remote port is
the first candidate of the machine's deterministic port chain that the
server accepts, and a candidate the server refuses is left behind for the
next one, so no port is ever chosen at random. A dropped connection is
re-established after the geometric backoff and starts the chain again at
its first candidate, so a reboot or a drop returns the machine to its
predictable number as soon as that port is free. Every port change is
saved to the state file and triggers a fresh System Metrics collection,
so the network report carries the current remote port of the machine
(docs/spec/port-forwarding-setup.md). A server whose address is
a local address of the machine itself, taken from ip -o addr, is
skipped, so the machine never tunnels onto itself. A vault without the
server group or the passphrase entry makes the service exit cleanly and
connect to nothing.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from pykeepass import PyKeePass

from pyntara import metrics
from pyntara.config import Config, load_config
from pyntara.forwarding_ports import candidate_ports
from pyntara.logger import configure_journal
from pyntara.logger import log_progress as _log
from pyntara.metrics_collect import trigger_collection
from pyntara.ssh import ssh_port_from_directives
from pyntara.ssh_access import host_from_address
from pyntara.utils import backoff_delay, substituted_command

# With -v the client prints a positive confirmation when the server
# accepted the requested remote port, so a forward is confirmed by its own
# success line instead of by waiting out a silence window.
SUCCESS_RE = re.compile(r"remote forward success for: listen (\d+)")
# A requested port that the server cannot bind makes ssh print this line
# and exit; the service reads it as taken there and walks to the next
# candidate of the chain instead of asking for a port of its own.
FAILED_RE = re.compile(r"remote port forwarding failed for listen port")


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


def own_addresses(cfg: Config) -> set[str]:
    """The machine's own IP addresses from the configured ip call.

    The addresses come from the local interfaces, both families, so a
    server address that appears here is the machine itself. A failed or
    missing ip call yields an empty set, so the filter then keeps every
    server: that errs toward forwarding instead of dropping a real
    server. The command and its timeout are values of the table.
    """

    pf = cfg.port_forwarding_setup
    try:
        result = subprocess.run(
            list(pf.own_addresses_command),
            capture_output=True,
            text=True,
            timeout=pf.own_addresses_timeout_seconds,
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
        if _normalize_host(host_from_address(server)) in own:
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


def _start_agent(
    cfg: Config,
    passphrase: str,
    key_path: Path,
) -> dict[str, str] | None:
    """Start a dedicated ssh-agent and unlock the key; the agent env or None.

    The key is passphrase-protected, so it is loaded into a dedicated
    ssh-agent through the configured askpass variables and a helper script
    that echoes the passphrase; after the load the helper is removed, and
    the long-running ssh processes sign through the agent without ever
    seeing the passphrase. A failed agent start or a failed unlock is
    logged and None is returned. agent_start_timeout_seconds bounds the
    ssh-agent start and key_unlock_timeout_seconds bounds the ssh-add
    unlock; askpass_helper_file_mode is the mode of the helper script,
    which must stay executable by its owner only, and askpass_display is
    the display ssh-add hands to that helper.
    """

    pf = cfg.port_forwarding_setup
    try:
        agent_out = subprocess.run(
            list(pf.agent_start_command),
            capture_output=True,
            text=True,
            timeout=pf.agent_start_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"cannot start the ssh-agent: {exc}")
        return None
    if agent_out.returncode != 0:
        _log(f"cannot start the ssh-agent: exited {agent_out.returncode}")
        return None
    socket_setting = pf.agent_socket_env_key + "="
    pid_setting = pf.agent_pid_env_key + "="
    env = dict(os.environ)
    for line in agent_out.stdout.splitlines():
        line = line.strip()
        if line.startswith(socket_setting):
            env[pf.agent_socket_env_key] = line.split("=", 1)[1].split(";", 1)[0]
        elif line.startswith(pid_setting):
            env[pf.agent_pid_env_key] = line.split("=", 1)[1].split(";", 1)[0]
    if pf.agent_socket_env_key not in env:
        _log("cannot start the ssh-agent: no socket reported")
        return None
    helper_dir = Path(tempfile.mkdtemp(prefix=pf.askpass_helper_dir_prefix))
    helper = helper_dir / pf.askpass_helper_file_name
    helper.write_text(pf.askpass_helper_content, encoding="utf-8")
    helper.chmod(pf.askpass_helper_file_mode)
    add_env = dict(env)
    for name, value in pf.askpass_env.items():
        add_env[name] = value.format(helper_path=str(helper))
    add_env[pf.display_env_key] = pf.askpass_display
    add_env[pf.passphrase_env_key] = passphrase
    try:
        added = subprocess.run(
            substituted_command(
                pf.key_add_command, {"key_path": str(key_path)}
            ),
            env=add_env,
            capture_output=True,
            text=True,
            timeout=pf.key_unlock_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"cannot unlock the port-forwarding key: {exc}")
        _remove_helper(helper_dir)
        _kill_agent(cfg, env)
        return None
    _remove_helper(helper_dir)
    if added.returncode != 0:
        _log(f"cannot unlock the port-forwarding key {key_path}")
        _kill_agent(cfg, env)
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


def _kill_agent(cfg: Config, env: dict[str, str]) -> None:
    """Kill the dedicated agent process, best effort.

    Called when the key unlock failed and the agent is useless, or when
    the service stops on its own; on a normal stop systemd kills the
    whole service control group, so the agent does not outlive the
    service either way. The variable that carries the process id and the
    signal are values of the table and of the standard library.
    """

    pf = cfg.port_forwarding_setup

    agent_pid = env.get(pf.agent_pid_env_key)
    if agent_pid:
        try:
            os.kill(int(agent_pid), signal.SIGTERM)
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
    of guessing. Every argument of the call is a value of the table, so no
    option of the tunnel lives in the module.
    """

    pf = cfg.port_forwarding_setup
    return substituted_command(
        pf.ssh_forward_command,
        {
            "ssh_port": str(ssh_port),
            "key_path": str(key_path),
            "remote_port": remote_port,
            "local_port": str(local_port),
            "user": user,
            "host": host_from_address(server),
            "remote_bind_address": pf.remote_bind_address,
            "server_alive_interval_seconds": str(
                pf.server_alive_interval_seconds
            ),
            "server_alive_count_max": str(pf.server_alive_count_max),
            "connect_timeout_seconds": str(pf.connect_timeout_seconds),
        },
    )


def _last_line(text: str) -> str:
    """The last non-empty line of ssh output, or an empty string.

    The line is what a person reads in the journal: ssh names the reason
    there (a refused host key, an unreachable host, a denied key), which
    is more useful than the exit code alone.
    """

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def start_forward(
    env: dict[str, str],
    cfg: Config,
    key_path: Path,
    ssh_port: int,
    server: str,
    user: str,
    remote_port: int,
    local_port: int,
    timeout_seconds: int,
) -> tuple[subprocess.Popen[str], bool, str | None]:
    """Start one -R ssh process and read stderr until the outcome is known.

    Returns (process, busy, error): busy is True when the server refused
    the requested port because it is taken there; error is None on
    success and a message otherwise. A forward is confirmed by its own
    success line, so the caller knows the tunnel is up without waiting
    out a silence window; the caller walks to the next candidate of the
    chain after a refusal and repairs any later drop.
    """

    command = _build_ssh_command(
        cfg, key_path, ssh_port, server, user, str(remote_port), local_port
    )
    poll_seconds = cfg.port_forwarding_setup.forward_outcome_poll_seconds
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
    busy = False
    error: str | None = None

    def _scan() -> bool:
        """Scan the buffer for an outcome; True when the wait is settled."""

        nonlocal busy, error
        if SUCCESS_RE.search(buffer):
            return True
        match = FAILED_RE.search(buffer)
        if match is not None:
            busy = True
            error = match.group(0)
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
            time.sleep(poll_seconds)
    # The process may have exited with output still buffered in the pipe;
    # drain it so a just-printed error is not lost.
    while True:
        try:
            chunk = proc.stderr.read()
        except (BlockingIOError, ValueError):
            chunk = ""
        if not chunk:
            break
        buffer += chunk
    if not _scan() and proc.poll() is not None:
        error = _last_line(buffer) or f"ssh exited {proc.returncode}"
    return proc, busy, error


def load_state(path: Path) -> dict[str, dict[str, int]]:
    """The recorded remote ports from the state file, or an empty dict.

    The state maps every server address to the remote port the machine
    currently holds for each local port. It is what the telemetry reads,
    not what the next attempt asks for: every attempt walks the chain
    from its first candidate again. A missing or unreadable file starts
    from an empty state, so the first accepted port of a run is reported
    as a change.
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


def save_state(
    cfg: Config, state: dict[str, dict[str, int]]
) -> None:
    """Persist the state atomically with root-only mode; errors are logged.

    The write goes through a temporary file in the same directory, so a
    crash never leaves a half-written state file behind. The suffix of
    that file, the mode of the state file and the indentation of its JSON
    are values of the table. The temporary file is removed whether the
    write succeeded or failed, and a failure to remove it is logged
    instead of raised, because the deployed service must keep running:
    a file left in the state directory beside the state file is exactly
    the leftover this cleanup is here to prevent.
    """

    pf = cfg.port_forwarding_setup
    path = pf.state_file_path
    temp = path.with_name(path.name + pf.state_temp_file_suffix)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps(state, ensure_ascii=False, indent=pf.state_json_indent),
            encoding="utf-8",
        )
        os.chmod(temp, pf.state_file_mode)
        os.replace(temp, path)
    except OSError as exc:
        _log(f"cannot save the port-forwarding state {path}: {exc}")
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError as exc:
            _log(f"cannot remove the temporary state file {temp}: {exc}")


def _open_tunnel(
    cfg: Config,
    server: str,
    ssh_port: int,
    local_port: int,
    key_path: Path,
    env: dict[str, str],
) -> tuple[subprocess.Popen[str], int] | None:
    """Walk the candidate chain until the server accepts a port.

    The walk starts at the first candidate of the machine on every call,
    so a reboot or a dropped connection starts the machine at its
    predictable number again. A candidate the server refuses is taken as
    busy there: the base pause of the table passes and the next candidate
    is tried, which keeps the server from a burst of attempts. A
    candidate that cannot be reached at all ends the walk, because a
    connection problem is not a busy port. Returns the live process with
    the accepted port, or None when no tunnel could be opened.
    """

    pf = cfg.port_forwarding_setup
    for port in candidate_ports(cfg, socket.gethostname()):
        proc, busy, error = start_forward(
            env,
            cfg,
            key_path,
            ssh_port,
            server,
            pf.remote_ssh_user,
            port,
            local_port,
            pf.connect_timeout_seconds,
        )
        if busy:
            _log(
                f"{server}: remote port {port} is taken there, "
                "trying the next candidate"
            )
            time.sleep(pf.backoff_base_seconds)
            continue
        if error is not None:
            _log(f"{server}: cannot connect for local {local_port}: {error}")
            return None
        return proc, port
    _log(
        f"{server}: every port of the range is taken, "
        "no tunnel for this attempt",
        priority=pf.error_priority,
    )
    return None


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

    Every connection attempt walks the machine's port chain from its
    first candidate, so the port of the tunnel is the first candidate the
    server accepts and never a port chosen at random. The accepted port
    is written into the state file, the record the telemetry and the
    System Metrics report read. A dropped connection or a walk that
    opened nothing is followed by the geometric backoff; the escalation
    resets after a connection that stayed up for at least the maximum
    backoff, so a single drop after a long uptime waits only the base
    pause.
    """

    pf = cfg.port_forwarding_setup
    reconnect = 0
    while True:
        try:
            opened = _open_tunnel(
                cfg, server, ssh_port, local_port, key_path, env
            )
            if opened is None:
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
            proc, port = opened
            changed_port = False
            with lock:
                if state.get(server, {}).get(str(local_port)) != port:
                    state.setdefault(server, {})[str(local_port)] = port
                    save_state(cfg, state)
                    changed_port = True
            if changed_port:
                trigger_collection(cfg)
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
    configure_journal(
        cfg.engine.with_journal_identifier(pf.journal_identifier)
    )
    kp = metrics.open_runtime_vault(cfg)
    if kp is None:
        _log(
            "cannot open the runtime vault; the service will be restarted",
            priority=pf.error_priority,
        )
        raise SystemExit(1)
    servers = read_server_addresses(kp, pf.vault_group_title)
    own = own_addresses(cfg)
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
    ssh_port = ssh_port_from_directives(cfg.ssh_daemon_setup)
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
    env = _start_agent(cfg, passphrase, key_path)
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
