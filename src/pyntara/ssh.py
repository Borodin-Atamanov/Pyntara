"""Shared SSH helpers: read the sshd listen port from the directives.

The i2pd and tor tasks forward to the local SSH daemon, so both need
the sshd listen port. The port lives only in the ssh_daemon_setup
directives, never duplicated into another config section; the single
reader lives here and is imported by every task that needs the value
(architecture contract section 3).
"""

from __future__ import annotations

from pyntara.config import SshDirective


def ssh_port_from_directives(directives: tuple[SshDirective, ...]) -> int:
    """The sshd Port directive value used as a forward target port.

    The value is read from the ssh_daemon_setup directives instead of
    being configured again, so the forward and the SSH daemon can never
    diverge. A missing or non-numeric Port is an error, because a
    forward to an unknown port is useless.
    """

    for directive in directives:
        if directive.name.casefold() == "port":
            try:
                return int(directive.value)
            except ValueError:
                raise RuntimeError(
                    "ssh_daemon_setup Port directive is not a number: "
                    f"{directive.value!r}"
                ) from None
    raise RuntimeError(
        "ssh_daemon_setup has no Port directive, cannot create the "
        "SSH forward"
    )
