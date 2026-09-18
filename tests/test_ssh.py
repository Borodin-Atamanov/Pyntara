"""Unit tests for the shared SSH reader of the listen port.

The reader is the single place that finds the port an address forward
targets, so the directive it reads is asserted here: the keyword is the
PORT_DIRECTIVE of the ssh_daemon_setup values and not a name written in
the code.
"""

from __future__ import annotations

import pytest

from pyntara import ssh
from pyntara.values import ssh_daemon_setup as values
from pyntara.values.ssh_daemon_setup import SshDirective


def test_the_port_directive_comes_from_the_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The keyword the reader looks for is PORT_DIRECTIVE: with two
    # directives carrying a port, the declared keyword decides which value
    # comes back.
    monkeypatch.setattr(
        values,
        "DIRECTIVES",
        (
            SshDirective(name="Port", value="2222"),
            SshDirective(name="ListenPort", value="30222"),
        ),
    )
    assert ssh.ssh_port_from_directives() == 2222
    monkeypatch.setattr(values, "PORT_DIRECTIVE", "ListenPort")
    assert ssh.ssh_port_from_directives() == 30222


def test_a_missing_port_directive_is_a_loud_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without the declared directive the reader cannot know the port the
    # forward targets, so it says so instead of guessing.
    monkeypatch.setattr(
        values,
        "DIRECTIVES",
        (SshDirective(name="PermitRootLogin", value="no"),),
    )
    with pytest.raises(RuntimeError, match="no Port directive"):
        ssh.ssh_port_from_directives()


def test_a_port_value_that_is_not_a_number_is_a_loud_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A directive whose value is not a number would be forwarded as a port
    # the daemon never listens on, so the reader refuses it and quotes the
    # value it read.
    monkeypatch.setattr(
        values,
        "DIRECTIVES",
        (SshDirective(name="Port", value="not-a-port"),),
    )
    with pytest.raises(RuntimeError, match="not a number"):
        ssh.ssh_port_from_directives()
