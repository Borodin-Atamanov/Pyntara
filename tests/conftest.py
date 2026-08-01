from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fast_kdbx(tmp_path: Path) -> tuple[Path, str]:
    """Create a KeePass database with AES-KDF (fast) for testing.

    Returns (vault_path, password) tuple. AES-KDF is used instead of the
    default Argon2 to avoid slow KDF computation during tests.
    """
    pykeepass = pytest.importorskip("pykeepass", reason="pykeepass is required")
    vault_path = tmp_path / "test.vault"
    password = "test-password-123"
    pykeepass.create_database(str(vault_path), password=password)
    return vault_path, password