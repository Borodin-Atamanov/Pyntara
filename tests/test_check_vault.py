"""Unit tests for the vault password check helper.

The check-vault command is the only path that opens KeePass databases from
the installer. Tests decrypt nothing: PyKeePass is mocked, because unit
tests must not depend on real vault files or real cryptography (developer
guide: all external resources are mocked).
"""

from __future__ import annotations

import pytest
from pykeepass.exceptions import CredentialsError
from typer.testing import CliRunner

from pyntara.pyntara import app, vault_password_is_correct

runner = CliRunner()


class _FakePyKeePass:
    """Stands in for pykeepass.PyKeePass and raises CredentialsError on demand."""

    def __init__(self, accept: bool) -> None:
        self._accept = accept

    def __call__(self, vault_path: str, password: str) -> None:
        if not self._accept:
            raise CredentialsError("Wrong password")


def _patch_pykeepass(monkeypatch: pytest.MonkeyPatch, accept: bool) -> None:
    monkeypatch.setattr("pyntara.pyntara.PyKeePass", _FakePyKeePass(accept))


def test_correct_password_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    # A password that opens the database must be reported as correct.
    _patch_pykeepass(monkeypatch, accept=True)
    assert vault_password_is_correct("vault.kdbx", "right") is True


def test_wrong_password_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # A wrong password raises CredentialsError inside PyKeePass; the helper
    # must convert that into False, never into an unhandled exception.
    _patch_pykeepass(monkeypatch, accept=False)
    assert vault_password_is_correct("vault.kdbx", "wrong") is False


def test_check_vault_command_reads_password_from_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # inst.sh sends the password through stdin, so the command must read it
    # there and exit 0 when the vault opens.
    _patch_pykeepass(monkeypatch, accept=True)
    result = runner.invoke(
        app, ["check-vault", "--vault", "vault.kdbx"], input="secret\n"
    )
    assert result.exit_code == 0


def test_check_vault_command_exits_one_for_wrong_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A wrong password must surface as exit code 1 so inst.sh can count the
    # attempt as failed.
    _patch_pykeepass(monkeypatch, accept=False)
    result = runner.invoke(
        app, ["check-vault", "--vault", "vault.kdbx"], input="wrong\n"
    )
    assert result.exit_code == 1


def test_bare_invocation_launches_run() -> None:
    # inst.sh launches `uv run pyntara` without arguments (bootstrap contract
    # section 6), so a bare invocation must run the engine stub and exit 0
    # instead of failing with "Missing command".
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Pyntara provisioning engine is not implemented yet." in result.stdout


def test_explicit_run_command_launches_run() -> None:
    # The run command stays reachable explicitly for direct invocation.
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0
    assert "Pyntara provisioning engine is not implemented yet." in result.stdout
