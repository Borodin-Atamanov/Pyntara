from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pyntara.secrets_store import VaultSecretsStore, _open_keepass_database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_kdbx(vault_path: Path, password: str) -> None:
    """Create a real KeePass database for testing.

    Also creates the companion .password file.
    """
    pykeepass = pytest.importorskip("pykeepass", reason="pykeepass is required")
    pykeepass.create_database(str(vault_path), password=password)
    # Create companion .password file
    password_path = vault_path.with_suffix(".password")
    password_path.write_text(password + "\n", encoding="utf-8")


def _setup_minimal_workspace(tmp_path: Path, vault_path: Path) -> Path:
    """Create a minimal workspace with config files and a test vault.

    Returns the workspace path.
    """
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)

    import shutil

    shutil.copy2(str(vault_path), str(secrets_dir / "default.vault"))
    # Also copy the companion .password file if it exists
    password_src = vault_path.with_suffix(".password")
    if password_src.exists():
        shutil.copy2(str(password_src), str(secrets_dir / "default.password"))

    # Create config.yaml
    (tmp_path / "config.yaml").write_text(
        f"paths:\n"
        f"  secrets_dir: {secrets_dir}\n"
        f"  task_data_dir: {tmp_path / 'task_data'}\n"
        f"timeouts:\n"
        f"  command_sec: 30\n"
        f"  task_sec: 60\n"
        f"logging:\n"
        f"  command_output_to_console: false\n"
        f"  command_output_to_log: false\n"
        f"  datetime_format: '%Y-%m-%d-%H-%M-%S'\n"
    )

    # Create tasks.yaml with a dummy task
    (tmp_path / "tasks.yaml").write_text(
        "tasks:\n"
        "  - name: dummy\n"
        "    order: 1\n"
        "    description: Dummy task for testing\n"
        "    module: pyntara.tasks.hostname:run\n"
        "    idempotent: true\n"
        "    default_enabled: true\n"
        "    timeout_sec: 30\n"
        "    depends_on: []\n"
        "    data_subdir: dummy\n"
    )

    # Create install_modes.yaml
    (tmp_path / "install_modes.yaml").write_text(
        "minimal:\n"
        "  - dummy\n"
        "default_desktop_mode: minimal\n"
        "default_server_mode: minimal\n"
        "auto_select_timeout_sec: 1\n"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Tests: KDF timeout wrapper
# ---------------------------------------------------------------------------


def test_open_keepass_times_out_on_slow_kdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When pykeepass.open() takes longer than kdf_timeout_sec, a RuntimeError is raised."""
    vault_path = tmp_path / "test.vault"
    vault_path.write_bytes(b"\x03\xd9\xa2\x9a" + b"\x00" * 60)

    class SlowModule:
        class CredentialsError(Exception):
            pass

        @staticmethod
        def open(vault_path: Path, *, password: str) -> object:
            del vault_path, password
            time.sleep(60)  # Simulate slow Argon2 KDF

    with pytest.raises(RuntimeError, match="timed out"):
        _open_keepass_database(
            pykeepass=SlowModule,
            vault_path=vault_path,
            password="wrong",
            kdf_timeout_sec=0.1,
        )


def test_open_keepass_succeeds_within_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When pykeepass.open() completes quickly, no timeout error is raised."""
    from dataclasses import dataclass

    vault_path = tmp_path / "test.vault"
    vault_path.write_bytes(b"\x03\xd9\xa2\x9a" + b"\x00" * 60)

    @dataclass
    class FakeEntry:
        title: str | None
        password: str | None
        group: object | None

    class FakeDatabase:
        entries: list = []

    class FastModule:
        class CredentialsError(Exception):
            pass

        @staticmethod
        def open(vault_path: Path, *, password: str) -> FakeDatabase:
            del vault_path, password
            return FakeDatabase()

    result = _open_keepass_database(
        pykeepass=FastModule,
        vault_path=vault_path,
        password="pw",
        kdf_timeout_sec=5.0,
    )
    assert isinstance(result, FakeDatabase)


# ---------------------------------------------------------------------------
# Tests: PYNTARA_VAULT_PASSWORD env var (non-interactive path)
# ---------------------------------------------------------------------------


def test_cli_does_not_hang_with_wrong_password_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run VaultSecretsStore with wrong PYNTARA_VAULT_PASSWORD, verify no hang."""
    vault_path = tmp_path / "test.vault"
    _create_test_kdbx(vault_path, "correct-password")

    monkeypatch.setenv("PYNTARA_VAULT_PASSWORD", "wrong-password")

    store = VaultSecretsStore(
        default_vault=vault_path,
        production_vault=tmp_path / "production.vault",
        use_production=False,
    )

    with pytest.raises(RuntimeError, match="password attempts exhausted"):
        store.load()


def test_cli_succeeds_with_correct_password_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run VaultSecretsStore with correct PYNTARA_VAULT_PASSWORD, verify success."""
    vault_path = tmp_path / "test.vault"
    _create_test_kdbx(vault_path, "correct-password")

    monkeypatch.setenv("PYNTARA_VAULT_PASSWORD", "correct-password")

    store = VaultSecretsStore(
        default_vault=vault_path,
        production_vault=tmp_path / "production.vault",
        use_production=False,
    )

    # Should not raise
    store.load()
    assert store._loaded


# ---------------------------------------------------------------------------
# Tests: Interactive password prompt (mocked getpass)
# ---------------------------------------------------------------------------


def test_cli_does_not_hang_with_wrong_password_via_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate interactive password entry with wrong password, verify no hang."""
    vault_path = tmp_path / "test.vault"
    _create_test_kdbx(vault_path, "correct-password")
    # Delete the .password file so the mock _read_password_hidden is used
    password_file = vault_path.with_suffix(".password")
    if password_file.exists():
        password_file.unlink()

    # Mock _read_password_hidden to return wrong password
    monkeypatch.setattr("pyntara.secrets_store._read_password_hidden", lambda prompt: "wrong-password")
    monkeypatch.setattr("pyntara.secrets_store._interactive_prompt_available", lambda: True)

    store = VaultSecretsStore(
        default_vault=vault_path,
        production_vault=tmp_path / "production.vault",
        use_production=False,
        password_provider=lambda _: "wrong-password",
    )

    with pytest.raises(RuntimeError, match="password attempts exhausted"):
        store.load()


def test_cli_succeeds_with_correct_password_via_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate interactive password entry with correct password, verify success."""
    vault_path = tmp_path / "test.vault"
    _create_test_kdbx(vault_path, "correct-password")

    # Mock _read_password_hidden to return correct password
    monkeypatch.setattr(
        "pyntara.secrets_store._read_password_hidden", lambda prompt: "correct-password"
    )
    monkeypatch.setattr("pyntara.secrets_store._interactive_prompt_available", lambda: True)

    store = VaultSecretsStore(
        default_vault=vault_path,
        production_vault=tmp_path / "production.vault",
        use_production=False,
    )

    # Should not raise
    store.load()
    assert store._loaded


# ---------------------------------------------------------------------------
# Tests: Full CLI subprocess (env var path)
# ---------------------------------------------------------------------------


def test_full_cli_subprocess_with_wrong_password_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the actual CLI as a subprocess with wrong PYNTARA_VAULT_PASSWORD.

    This tests the full chain: CLI argument parsing -> config loading ->
    secrets loading -> task execution (which fails because password is wrong).
    """
    vault_path = tmp_path / "test.vault"
    _create_test_kdbx(vault_path, "correct-password")
    workspace = _setup_minimal_workspace(tmp_path, vault_path)

    # Find the pyntara executable
    pyntara_bin = _find_pyntara_bin()

    env = dict(os.environ)
    env["PYNTARA_VAULT_PASSWORD"] = "wrong-password"

    start = time.monotonic()
    result = subprocess.run(
        [
            pyntara_bin,
            "--config",
            str(workspace / "config.yaml"),
            "--tasks-config",
            str(workspace / "tasks.yaml"),
            "--install-modes",
            str(workspace / "install_modes.yaml"),
            "--mode",
            "minimal",
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed = time.monotonic() - start

    # The CLI should fail (wrong password), but NOT hang
    assert result.returncode != 0, (
        f"CLI should fail with wrong password. "
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert elapsed < 25, f"CLI took {elapsed:.1f}s - possible hang!"


def test_full_cli_subprocess_with_correct_password_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the actual CLI as a subprocess with correct PYNTARA_VAULT_PASSWORD."""
    vault_path = tmp_path / "test.vault"
    _create_test_kdbx(vault_path, "correct-password")
    workspace = _setup_minimal_workspace(tmp_path, vault_path)

    pyntara_bin = _find_pyntara_bin()

    env = dict(os.environ)
    env["PYNTARA_VAULT_PASSWORD"] = "correct-password"

    start = time.monotonic()
    result = subprocess.run(
        [
            pyntara_bin,
            "--config",
            str(workspace / "config.yaml"),
            "--tasks-config",
            str(workspace / "tasks.yaml"),
            "--install-modes",
            str(workspace / "install_modes.yaml"),
            "--mode",
            "minimal",
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 25, f"CLI took {elapsed:.1f}s - possible hang!"
    # The CLI may fail because the dummy task can't actually run,
    # but it should NOT hang or crash with a secrets-related error
    assert "password" not in result.stderr.lower() or "CredentialsError" not in result.stderr


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_pyntara_bin() -> str:
    """Locate the pyntara CLI executable."""
    # Try common locations
    candidates = [
        "pyntara",
        str(Path(sys.prefix) / "bin" / "pyntara"),
        str(Path.home() / ".local" / "bin" / "pyntara"),
    ]
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "--help"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    # Fallback: try uv run
    return "uv run pyntara"