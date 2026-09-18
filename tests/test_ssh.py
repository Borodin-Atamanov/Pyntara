"""Unit tests for the shared SSH reader of the listen port.

The reader is the single place that finds the port an address forward
targets, so the directive it reads is asserted here: the keyword is the
port_directive of the ssh_daemon_setup table and not a name written in
the code.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from support import make_config

from pyntara import ssh
from pyntara.config import SshDirective


def test_the_port_directive_comes_from_the_config() -> None:
    # The keyword the reader looks for is the port_directive of the table:
    # with two directives carrying a port, the configured one decides
    # which value comes back, and the shipped keyword finds the other.
    section = make_config().ssh_daemon_setup
    two_ports = replace(
        section,
        directives=(
            SshDirective(name="Port", value="2222"),
            SshDirective(name="ListenPort", value="30222"),
        ),
    )
    assert ssh.ssh_port_from_directives(two_ports) == 2222
    assert (
        ssh.ssh_port_from_directives(replace(two_ports, port_directive="ListenPort"))
        == 30222
    )


def test_a_missing_port_directive_is_a_loud_error() -> None:
    # Without the configured directive the reader cannot know the port the
    # forward targets, so it says so instead of guessing.
    section = replace(
        make_config().ssh_daemon_setup,
        directives=(SshDirective(name="PermitRootLogin", value="no"),),
    )
    with pytest.raises(RuntimeError, match="no Port directive"):
        ssh.ssh_port_from_directives(section)


def test_a_port_value_that_is_not_a_number_is_a_loud_error() -> None:
    # A directive whose value is not a number would be forwarded as a port
    # the daemon never listens on, so the reader refuses it and quotes the
    # value it read.
    section = replace(
        make_config().ssh_daemon_setup,
        directives=(SshDirective(name="Port", value="not-a-port"),),
    )
    with pytest.raises(RuntimeError, match="not a number"):
        ssh.ssh_port_from_directives(section)
