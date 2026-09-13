"""Shared SSH helpers: read the sshd listen port from the directives.

The i2pd and tor tasks forward to the local SSH daemon, so both need
the sshd listen port. The port lives only in the ssh_daemon_setup
directives, never duplicated into another config section; the single
reader lives here and is imported by every task that needs the value
(architecture contract, Configuration).
"""

from __future__ import annotations

from pyntara.config import SshDaemonSetupConfig


def ssh_port_from_directives(cfg: SshDaemonSetupConfig) -> int:
    """The sshd listen port used as a forward target port.

    The value is read from the ssh_daemon_setup directives instead of
    being configured again, so the forward and the SSH daemon can never
    diverge. The directive that carries it is the port_directive of the
    same table, compared without case. A missing or non-numeric value is
    an error, because a forward to an unknown port is useless.
    """

    for directive in cfg.directives:
        if directive.name.casefold() == cfg.port_directive.casefold():
            try:
                return int(directive.value)
            except ValueError:
                raise RuntimeError(
                    "ssh_daemon_setup "
                    f"{cfg.port_directive} directive is not a number: "
                    f"{directive.value!r}"
                ) from None
    raise RuntimeError(
        f"ssh_daemon_setup has no {cfg.port_directive} directive, cannot "
        "create the SSH forward"
    )
