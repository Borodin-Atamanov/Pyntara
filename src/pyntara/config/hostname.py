"""[hostname] table parser.

The section carries the parameters of the hostname task: where the
hostname file lives and which command applies the name to the running
kernel.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._fields import ConfigError, _nonempty_string_field


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


def _hostname_table(raw: object) -> HostnameConfig:
    """Validate the [hostname] table and build HostnameConfig.

    hostname_file is a non-empty string; set_hostname_command is a
    non-empty array of non-empty strings.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[hostname] section is missing or not a table")
    hostname_file = _nonempty_string_field(
        raw.get("hostname_file"), "hostname.hostname_file"
    )
    command = raw.get("set_hostname_command")
    if not isinstance(command, list) or not command:
        raise ConfigError(
            "hostname.set_hostname_command must be a non-empty array of strings"
        )
    if not all(isinstance(part, str) and part.strip() for part in command):
        raise ConfigError(
            "hostname.set_hostname_command must be non-empty strings"
        )
    return HostnameConfig(
        hostname_file=hostname_file,
        set_hostname_command=tuple(part.strip() for part in command),
    )
