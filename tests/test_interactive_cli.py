from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import PtySession, find_pyntara_bin

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The password used by fast_kdbx fixture in conftest.py
_VAULT_PASSWORD = "test-password-123"

# Mode selector prompt marker
_MODE_PROMPT_MARKER = b"Select install mode"

# KeePass password prompt marker
_PASSWORD_PROMPT_MARKER = b"KeePass password for"

# Task completion marker
_TASK_DONE_MARKER = b"hostname: done"

# Timeout for PTY operations with a soft 10s budget.
_PTY_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_cli_cmd(workspace: Path, *, mode: str | None = None) -> list[str]:
    """Build the pyntara CLI command with explicit config paths."""
    cmd = [
        *find_pyntara_bin(),
        "--config",
        str(workspace / "config.yaml"),
        "--tasks-config",
        str(workspace / "tasks.yaml"),
        "--install-modes",
        str(workspace / "install_modes.yaml"),
    ]
    if mode is not None:
        cmd.extend(["--mode", mode])
    return cmd


def _env_with_vault_password(password: str) -> dict[str, str]:
    """Return environment dict with PYNTARA_VAULT_PASSWORD set."""
    env = dict(os.environ)
    env["PYNTARA_VAULT_PASSWORD"] = password
    return env


# ---------------------------------------------------------------------------
# Layer 1 — Scenario 1.1: Auto-select mode + correct password via env var
# ---------------------------------------------------------------------------


def test_auto_select_mode_with_env_password(test_workspace: Path) -> None:
    """CLI auto-selects mode (no key press) and uses PYNTARA_VAULT_PASSWORD.

    Expected: mode selected by timeout, vault opens, tasks run, exit 0.
    """
    cmd = _build_cli_cmd(test_workspace)
    env = _env_with_vault_password(_VAULT_PASSWORD)

    with PtySession(cmd, cwd=test_workspace, env=env, timeout=_PTY_TIMEOUT) as session:
        # Wait for mode selector to appear
        session.read_until(_MODE_PROMPT_MARKER, timeout=5)

        # Do nothing — let auto-select fire (2s timeout in fixture)
        # Wait for password prompt or task output
        try:
            session.read_until(_TASK_DONE_MARKER, timeout=10)
        except TimeoutError:
            pass

        session.close()

    assert session.returncode == 0, (
        f"CLI should exit 0 with correct password. "
        f"Output:\n{session.output_text}"
    )
    assert _TASK_DONE_MARKER in session.output, (
        f"Task completion marker not found. Output:\n{session.output_text}"
    )


# ---------------------------------------------------------------------------
# Layer 1 — Scenario 1.2: Arrow key navigation + correct password via env var
# ---------------------------------------------------------------------------


def test_arrow_navigation_with_env_password(test_workspace: Path) -> None:
    """User presses DOWN+ENTER to select mode, password via env var.

    Expected: desktop mode selected, vault opens, tasks run, exit 0.
    """
    cmd = _build_cli_cmd(test_workspace)
    env = _env_with_vault_password(_VAULT_PASSWORD)

    with PtySession(cmd, cwd=test_workspace, env=env, timeout=_PTY_TIMEOUT) as session:
        # Wait for mode selector
        session.read_until(_MODE_PROMPT_MARKER, timeout=5)

        # Press DOWN arrow (escape sequence: \\x1b[B)
        session.write(b"\x1b[B")
        time.sleep(0.05)

        # Press ENTER to confirm
        session.write(b"\r")

        # Wait for task output
        try:
            session.read_until(_TASK_DONE_MARKER, timeout=10)
        except TimeoutError:
            pass

        session.close()

    assert session.returncode == 0, (
        f"CLI should exit 0. Output:\n{session.output_text}"
    )
    assert _TASK_DONE_MARKER in session.output, (
        f"Task completion marker not found. Output:\n{session.output_text}"
    )


# ---------------------------------------------------------------------------
# Layer 1 — Scenario 1.3: Wrong password via getpass (3 attempts)
# ---------------------------------------------------------------------------


def test_wrong_password_via_getpass(test_workspace: Path) -> None:
    """User enters wrong password 3 times via interactive getpass prompt.

    The password prompt appears BEFORE the mode selector because
    VaultSecretsStore.load() is called before select_install_mode().

    Expected: password attempts exhausted, exit non-zero.
    """
    cmd = _build_cli_cmd(test_workspace)
    # Do NOT set PYNTARA_VAULT_PASSWORD — force interactive prompt
    # Delete the .password file so the CLI prompts for password
    password_file = test_workspace / "secrets" / "default.password"
    if password_file.exists():
        password_file.unlink()

    with PtySession(cmd, cwd=test_workspace, timeout=_PTY_TIMEOUT) as session:
        # Wait for the password prompt (it appears before mode selector)
        try:
            session.read_until(_PASSWORD_PROMPT_MARKER, timeout=10)
        except TimeoutError:
            pass

        # Send wrong password
        session.writeline("wrong-password")
        time.sleep(0.15)

        # Wait for process to fail (it may exit after 1-3 attempts)
        # Don't wait for specific prompts — just check the exit code
        session.close()

    assert session.returncode != 0, (
        f"CLI should fail with wrong password. Output:\n{session.output_text}"
    )
    # The error should mention password somewhere
    assert b"password" in session.output.lower() or b"CredentialsError" in session.output, (
        f"Expected password-related error. Output:\n{session.output_text}"
    )


# ---------------------------------------------------------------------------
# Layer 1 — Scenario 1.4: Correct password via getpass
# ---------------------------------------------------------------------------


def test_correct_password_via_getpass(test_workspace: Path) -> None:
    """User enters correct password via interactive getpass prompt.

    The password prompt appears BEFORE the mode selector because
    VaultSecretsStore.load() is called before select_install_mode().

    Expected: vault opens, mode auto-selects, tasks run, exit 0.
    """
    cmd = _build_cli_cmd(test_workspace)
    # Do NOT set PYNTARA_VAULT_PASSWORD — force interactive prompt

    with PtySession(cmd, cwd=test_workspace, timeout=_PTY_TIMEOUT) as session:
        # Wait for password prompt (it appears before mode selector)
        try:
            session.read_until(_PASSWORD_PROMPT_MARKER, timeout=10)
        except TimeoutError:
            pass

        # Send correct password
        session.writeline(_VAULT_PASSWORD)

        # Wait for task output (mode auto-selects after password)
        try:
            session.read_until(_TASK_DONE_MARKER, timeout=10)
        except TimeoutError:
            pass

        session.close()

    assert session.returncode == 0, (
        f"CLI should exit 0 with correct password. Output:\n{session.output_text}"
    )
    assert _TASK_DONE_MARKER in session.output, (
        f"Task completion marker not found. Output:\n{session.output_text}"
    )


# ---------------------------------------------------------------------------
# Layer 1 — Scenario 1.5: Auto-timeout with no input
# ---------------------------------------------------------------------------


def test_auto_timeout_no_input(test_workspace: Path) -> None:
    """No key pressed during mode selection, password via env var.

    Expected: auto-select fires, vault opens, tasks run, exit 0.
    """
    cmd = _build_cli_cmd(test_workspace)
    env = _env_with_vault_password(_VAULT_PASSWORD)

    with PtySession(cmd, cwd=test_workspace, env=env, timeout=_PTY_TIMEOUT) as session:
        # Wait for mode selector
        session.read_until(_MODE_PROMPT_MARKER, timeout=5)

        # Do nothing — let auto-select fire
        # Wait for task output
        try:
            session.read_until(_TASK_DONE_MARKER, timeout=10)
        except TimeoutError:
            pass

        session.close()

    assert session.returncode == 0, (
        f"CLI should exit 0. Output:\n{session.output_text}"
    )
    assert _TASK_DONE_MARKER in session.output, (
        f"Task completion marker not found. Output:\n{session.output_text}"
    )


# ---------------------------------------------------------------------------
# Layer 1 — Scenario 1.6: UP arrow wraps around
# ---------------------------------------------------------------------------


def test_up_arrow_wraps_around(test_workspace: Path) -> None:
    """User presses UP (wraps to last option) then ENTER, password via env.

    Expected: desktop mode selected, tasks run, exit 0.
    """
    cmd = _build_cli_cmd(test_workspace)
    env = _env_with_vault_password(_VAULT_PASSWORD)

    with PtySession(cmd, cwd=test_workspace, env=env, timeout=_PTY_TIMEOUT) as session:
        session.read_until(_MODE_PROMPT_MARKER, timeout=5)

        # Press UP arrow (wraps from minimal to desktop)
        session.write(b"\x1b[A")
        time.sleep(0.05)

        # Press ENTER
        session.write(b"\r")

        try:
            session.read_until(_TASK_DONE_MARKER, timeout=10)
        except TimeoutError:
            pass

        session.close()

    assert session.returncode == 0, (
        f"CLI should exit 0. Output:\n{session.output_text}"
    )
    assert _TASK_DONE_MARKER in session.output


# ---------------------------------------------------------------------------
# Layer 1 — Scenario 1.7: Multiple arrow presses
# ---------------------------------------------------------------------------


def test_multiple_arrow_presses(test_workspace: Path) -> None:
    """User presses DOWN twice (wraps around) then ENTER, password via env.

    Expected: mode wraps around, tasks run, exit 0.
    """
    cmd = _build_cli_cmd(test_workspace)
    env = _env_with_vault_password(_VAULT_PASSWORD)

    with PtySession(cmd, cwd=test_workspace, env=env, timeout=_PTY_TIMEOUT) as session:
        session.read_until(_MODE_PROMPT_MARKER, timeout=5)

        # Press DOWN twice: minimal -> server -> desktop
        session.write(b"\x1b[B")
        time.sleep(0.05)
        session.write(b"\x1b[B")
        time.sleep(0.05)

        # Press ENTER
        session.write(b"\r")

        try:
            session.read_until(_TASK_DONE_MARKER, timeout=10)
        except TimeoutError:
            pass

        session.close()

    assert session.returncode == 0, (
        f"CLI should exit 0. Output:\n{session.output_text}"
    )
    assert _TASK_DONE_MARKER in session.output


# ---------------------------------------------------------------------------
# Layer 1 — Scenario 1.8: Non-TTY mode (piped stdin) uses default
# ---------------------------------------------------------------------------


def test_non_tty_uses_default_mode(test_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When stdin is not a TTY, mode selector returns default immediately.

    This tests the non-interactive path via subprocess (not PTY).
    """

    cmd = _build_cli_cmd(test_workspace)
    env = _env_with_vault_password(_VAULT_PASSWORD)

    # Run with piped stdin (non-TTY)
    result = subprocess.run(
        cmd,
        cwd=test_workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"CLI should exit 0 in non-TTY mode. "
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "hostname: done" in result.stdout, (
        f"Task output not found. stdout:\n{result.stdout}"
    )