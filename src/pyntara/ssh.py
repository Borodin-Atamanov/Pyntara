"""Shared SSH helpers: read the sshd listen port from the declared directives.

The i2pd and tor tasks forward to the local SSH daemon, so both need
the sshd listen port. The port lives only in the DIRECTIVES of the
ssh_daemon_setup values, never duplicated into another section; the single
reader lives here and is imported by every task and command that needs the
value (architecture contract, Configuration).
"""

from __future__ import annotations

from pyntara.values import ssh_daemon_setup as values


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
