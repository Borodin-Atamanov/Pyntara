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

# Spellings an OpenSSH tool prints for a value it normalizes, as pairs of the
# declared value and the spellings that mean the same. The daemon accepts
# prohibit-password for the root login and prints its deprecated alias
# without-password, so a comparison against the declared word alone reports a gap
# on a machine that is configured exactly as intended, and that false finding
# hides the real ones behind it (measured 2026-10-14 on OpenSSH 9.6p1
# Ubuntu-3ubuntu13.19: a configuration file carrying PermitRootLogin
# prohibit-password answers permitrootlogin without-password, while one carrying
# yes answers yes).
VALUE_EQUIVALENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prohibit-password", ("without-password",)),
)


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


def _values_match(
    declared: str,
    reported: str,
) -> bool:
    """True when the tool reports a value that means the declared one.

    A tool normalizes some values in its own output, so a comparison against the
    declared word alone would report a gap on a machine that is configured
    exactly as intended and would hide the gaps behind it.
    """

    wanted = declared.casefold()
    seen = reported.casefold()
    if seen == wanted:
        return True
    for value, equivalents in VALUE_EQUIVALENTS:
        if value.casefold() == wanted:
            return seen in {equivalent.casefold() for equivalent in equivalents}
    return False


def effective_config_errors(
    config_command: tuple[str, ...],
    directives: tuple[SshDirective, ...],
    timeout: float,
    tool_label: str,
) -> list[str]:
    """One sentence per configured directive the configuration does not carry.

    The configured probe command prints the effective configuration after every
    file of the Include chain is applied, so the check is independent of the
    version and of other files in the drop-in directory: a directive that a
    later file overrides, or a keyword the tool does not know, is reported
    instead of being silently accepted. The label names the tool in the
    messages, "ssh -G" for the client and "sshd -T" for the daemon, and a value
    the tool prints in another spelling is read through VALUE_EQUIVALENTS.

    Every finding is returned rather than the first one, because a single
    sentence hides the rest: measured 2026-10-14 on a machine where the daemon
    prints the deprecated alias of the root login setting, the first finding was
    that alias and the password authentication that really was overridden by
    another file never reached the report.
    """

    try:
        result = run_command(
            list(config_command),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"cannot run {tool_label}: {exc}"]
    if result.returncode != 0:
        return [f"{tool_label} exited {result.returncode}: {result.stderr.strip()}"]
    effective: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(" ")
        if sep:
            effective[key.casefold()] = value.strip()
    findings: list[str] = []
    for directive in directives:
        key = directive.name.casefold()
        actual = effective.get(key)
        if actual is not None and _values_match(directive.value, actual):
            continue
        findings.append(
            f"{tool_label} reports {key} as {actual or 'unset'}, "
            f"expected {directive.value}"
        )
    return findings
