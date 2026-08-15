"""Unit tests for the shared I2P helpers (pyntara.i2pd)."""

from __future__ import annotations

from pathlib import Path

from support import (
    I2PD_KEYS_IDENTITY_SIZE,
    i2pd_keys_b32_address,
    i2pd_keys_file_bytes,
)

from pyntara.i2pd import b32_address


def test_b32_address_from_keys(tmp_path: Path) -> None:
    # The .b32.i2p address is the unpadded lowercase base32 of the
    # SHA-256 hash of the IdentityEx record at the start of the keys
    # file.
    keys = tmp_path / "ssh.dat"
    keys.write_bytes(i2pd_keys_file_bytes())
    assert b32_address(keys) == i2pd_keys_b32_address()


def test_b32_address_missing_or_broken(tmp_path: Path) -> None:
    # A missing, too short or non-KEY keys file yields None instead of an
    # error, so the caller reports that the address is not available yet.
    missing = tmp_path / "missing.dat"
    assert b32_address(missing) is None
    empty = tmp_path / "empty.dat"
    empty.write_text("", encoding="utf-8")
    assert b32_address(empty) is None
    short = tmp_path / "short.dat"
    short.write_bytes(b"x" * (I2PD_KEYS_IDENTITY_SIZE - 1))
    assert b32_address(short) is None
    wrong_cert = tmp_path / "wrong-cert.dat"
    data = bytearray(i2pd_keys_file_bytes())
    data[I2PD_KEYS_IDENTITY_SIZE - 3] = 0  # NULL certificate, not KEY
    wrong_cert.write_bytes(bytes(data))
    assert b32_address(wrong_cert) is None
    truncated = tmp_path / "truncated.dat"
    truncated.write_bytes(i2pd_keys_file_bytes()[: I2PD_KEYS_IDENTITY_SIZE + 2])
    assert b32_address(truncated) is None
