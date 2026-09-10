"""[hostname] table parser.

The section carries the parameters of the hostname task: where the
hostname file lives and which command applies the name to the running
kernel.
"""


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostnameConfig:
    """Hostname task parameters.

    hostname_file is the path of the file that holds the hostname;
    set_hostname_command is the command that applies the name to the
    running kernel, so socket.gethostname() returns it for the dependent
    tasks.
    """

    hostname_file: str
    set_hostname_command: tuple[str, ...]
