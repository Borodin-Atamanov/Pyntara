"""Unit tests for the shared SSH reader of the listen port and of the

effective configuration.

The reader is the single place that finds the port an address forward
targets, so the directive it reads is asserted here: the keyword is the
PORT_DIRECTIVE of the ssh_daemon_setup values and not a name written in
the code. The effective configuration check lives here as well, because
two sections use it with different tools and the rules it applies are
facts about those tools.
"""

from __future__ import annotations

import pytest
from support import FakeProc

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


def _effective_config_from(
    monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    """Answer the probe command with a fixed effective configuration."""

    monkeypatch.setattr(
        ssh,
        "run_command",
        lambda command, **kwargs: FakeProc(0, output),
    )


def test_a_spelling_the_tool_normalizes_is_not_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The daemon accepts prohibit-password for the root login and prints its
    # deprecated alias without-password. Comparing the text alone reported a
    # gap on a machine that is configured exactly as intended, and that false
    # finding stood in front of the real ones (measured 2026-10-14 on
    # OpenSSH 9.6p1 Ubuntu-3ubuntu13.19), so the pair is declared in the shared
    # module and read from there.
    declared, equivalents = ssh.VALUE_EQUIVALENTS[0]
    _effective_config_from(monkeypatch, f"permitrootlogin {equivalents[0]}\n")
    directives = (SshDirective("PermitRootLogin", declared),)
    assert (
        ssh.effective_config_errors(("sshd", "-T"), directives, 60.0, "sshd -T")
        == []
    )
    _effective_config_from(monkeypatch, "permitrootlogin yes\n")
    assert ssh.effective_config_errors(
        ("sshd", "-T"), directives, 60.0, "sshd -T"
    ) == [
        (
            "sshd -T reports permitrootlogin as yes, "
            f"expected {declared}"
        )
    ]


def test_every_directive_the_configuration_lacks_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A single sentence hides the rest: on 2026-10-14 the false finding about
    # the alias was the first one and the password authentication that really
    # was overridden by another file never reached the report.
    _effective_config_from(
        monkeypatch,
        "port 30222\npermitrootlogin yes\npasswordauthentication yes\n",
    )
    directives = (
        SshDirective("Port", "30222"),
        SshDirective("PermitRootLogin", "prohibit-password"),
        SshDirective("PasswordAuthentication", "no"),
    )
    findings = ssh.effective_config_errors(
        ("sshd", "-T"), directives, 60.0, "sshd -T"
    )
    assert findings == [
        (
            "sshd -T reports permitrootlogin as yes, "
            "expected prohibit-password"
        ),
        "sshd -T reports passwordauthentication as yes, expected no",
    ]


def test_a_directive_the_configuration_does_not_carry_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _effective_config_from(monkeypatch, "port 30222\n")
    findings = ssh.effective_config_errors(
        ("sshd", "-T"),
        (SshDirective("ClientAliveInterval", "60"),),
        60.0,
        "sshd -T",
    )
    assert findings == [
        "sshd -T reports clientaliveinterval as unset, expected 60"
    ]
