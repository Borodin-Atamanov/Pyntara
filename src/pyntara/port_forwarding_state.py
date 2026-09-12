"""Command: the forwarded ports of this machine and their ssh commands.

The System Metrics collector runs this command as its port_forwarding
network module, so every network report carries the current forwarding
state and the ssh command that reaches this machine through each server.
The command reads the root-only state file written by the
auto_port_forwarding service and prints one JSON record per server and
forwarded local port: the server host, the local port, the granted
remote port and the ssh command that connects to the server on that
port, where the reverse tunnel delivers the connection to the local SSH
daemon. A missing state file means no port forwarding is configured and
prints nothing with exit code 0, so a machine without the vault data
shows an empty module instead of an error. A corrupt state file is
reported as an error, never silently dropped.

The state file path comes from the single system config the command is
given, which is the same value the service writes and the task deploys,
so the report and the tunnels can never disagree
(docs/spec/system-metrics.md, section Report collector). Runs as
`python -m pyntara.port_forwarding_state CONFIG_PATH`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pyntara.config import Config, absent_config_keys, load_config
from pyntara.ssh_access import host_from_address, ssh_command


def local_port_number(value: object) -> int | str:
    """The local port as a number when the state file wrote one.

    The keys of a JSON object are strings, so the local port arrives as
    text; a value that is a port becomes a number and anything else
    stays as it was written, without guessing.
    """

    try:
        return int(str(value))
    except ValueError:
        return str(value)


def state_records(cfg: Config, raw: object) -> list[dict[str, object]]:
    """One record per server and local port of the parsed state file.

    A malformed entry is skipped instead of raising: the file is written
    by the service, and one unreadable entry must not hide the rest of
    the forwarding state. The channel name, the field names and the ssh
    command come from the config.
    """

    engine = cfg.engine
    keys = engine.report_record_keys
    channel = cfg.port_forwarding_setup.report_channel_name
    records: list[dict[str, object]] = []
    if not isinstance(raw, dict):
        return records
    for server, ports in raw.items():
        if not isinstance(server, str) or not isinstance(ports, dict):
            continue
        for written_local_port, remote_port in ports.items():
            if not isinstance(remote_port, int) or isinstance(remote_port, bool):
                continue
            host = host_from_address(server)
            records.append(
                {
                    keys["channel"]: channel,
                    keys["server"]: host,
                    keys["local_port"]: local_port_number(written_local_port),
                    keys["remote_port"]: remote_port,
                    keys["ssh"]: ssh_command(engine, host, remote_port),
                }
            )
    return records


def main(argv: list[str]) -> int:
    """Print the state records; a missing file prints nothing.

    Returns 0 on success and when no forwarding is configured, 2 on a
    wrong argument count and 1 on an unreadable or corrupt state file.
    """

    if len(argv) != 2:
        print(f"usage: {argv[0]} CONFIG_PATH", file=sys.stderr)
        return 2
    cfg = load_config(Path(argv[1]))
    missing = absent_config_keys(
        cfg.port_forwarding_setup, ("state_file_path",)
    )
    if missing:
        print(
            "error: the port_forwarding_setup section of the config has no "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    path = cfg.port_forwarding_setup.state_file_path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    except (ValueError, OSError) as exc:
        print(
            f"cannot read the port-forwarding state {path}: {exc}",
            file=sys.stderr,
        )
        return 1
    records = state_records(cfg, raw)
    if not records:
        return 0
    print(
        json.dumps(records, ensure_ascii=False, indent=cfg.engine.report_json_indent)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
