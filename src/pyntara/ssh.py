"""Shared SSH helpers: read the sshd listen port from the declared directives.

The i2pd and tor tasks forward to the local SSH daemon, so both need
the sshd listen port. The port lives only in the DIRECTIVES of the
ssh_daemon_setup values, never duplicated into another section; the single
reader lives here and is imported by every task and command that needs the
value (architecture contract, Configuration).
"""

from __future__ import annotations

import subprocess

from pyntara.utils import run_command
from pyntara.values import ssh_daemon_setup as values
from pyntara.values.common import SshDirective


def ssh_port_from_directives() -> int:
    """The sshd listen port used as a forward target port.

    The value is read from the declared ssh_daemon_setup directives instead
    of being configured again, so the forward and the SSH daemon can never
    diverge. The directive that carries it is PORT_DIRECTIVE, compared
    without case. A missing or non-numeric value is an error, because a
    forward to an unknown port is useless.
    """

    for directive in values.DIRECTIVES:
        if directive.name.casefold() == values.PORT_DIRECTIVE.casefold():
            try:
                return int(directive.value)
            except ValueError:
                raise RuntimeError(
                    "ssh_daemon_setup "
                    f"{values.PORT_DIRECTIVE} directive is not a number: "
                    f"{directive.value!r}"
                ) from None
    raise RuntimeError(
        f"ssh_daemon_setup has no {values.PORT_DIRECTIVE} directive, cannot "
        "create the SSH forward"
    )


def effective_config_errors(
    config_command: tuple[str, ...],
    directives: tuple[SshDirective, ...],
    timeout: float,
    tool_label: str,
) -> str | None:
    """Error text when a configured directive is not effective; None when OK.

    The configured probe command prints the effective configuration after every
    file of the Include chain is applied, so the check is independent of the
    version and of other files in the drop-in directory: a directive that a
    later file overrides, or a keyword the tool does not know, is reported as an
    error instead of being silently accepted. The label names the tool in the
    messages, "ssh -G" for the client and "sshd -T" for the daemon.
    """

    try:
        result = run_command(
            list(config_command),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cannot run {tool_label}: {exc}"
    if result.returncode != 0:
        return f"{tool_label} exited {result.returncode}: {result.stderr.strip()}"
    effective: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(" ")
        if sep:
            effective[key.casefold()] = value.strip()
    for directive in directives:
        key = directive.name.casefold()
        actual = effective.get(key)
        if actual is None or actual.casefold() != directive.value.casefold():
            return (
                f"{tool_label} reports {key} as {actual or 'unset'}, "
                f"expected {directive.value}"
            )
    return None
