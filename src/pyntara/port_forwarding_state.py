"""Deployed command that prints the current port-forwarding state.

The System Metrics collector runs this command as its port_forwarding
network module, so every network report carries the assigned remote
ports of the machine. The command reads the root-only state file written
by the auto_port_forwarding service and prints one line per server and
forwarded local port; a missing state file means no port forwarding is
configured and prints nothing with exit code 0, so a machine without the
vault data shows an empty module instead of an error. A corrupt state
file is reported as an error, never silently dropped. Runs as
`python -m pyntara.port_forwarding_state <state_file_path>`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def render_state(path: Path) -> str:
    """The port-forwarding state as printable lines, or an empty string.

    A missing file yields an empty string, because no forwarding is
    configured; a file that cannot be parsed raises ValueError, so the
    caller reports the corruption as an error instead of dropping it.
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    lines: list[str] = []
    for server, ports in raw.items():
        if not isinstance(ports, dict):
            continue
        for local_port, remote_port in ports.items():
            if isinstance(remote_port, int) and not isinstance(remote_port, bool):
                lines.append(
                    f"{server}: local {local_port} to remote {remote_port}"
                )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """Print the state file content; a missing file prints nothing.

    Returns 0 on success and when no forwarding is configured, 2 on a
    missing argument and 1 on an unreadable or corrupt state file.
    """

    if len(argv) < 2:
        print("error: missing state file path argument", file=sys.stderr)
        return 2
    path = Path(argv[1])
    try:
        text = render_state(path)
    except (ValueError, OSError) as exc:
        print(
            f"cannot read the port-forwarding state {path}: {exc}",
            file=sys.stderr,
        )
        return 1
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
