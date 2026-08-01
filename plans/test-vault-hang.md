# Plan: Testing KeePass vault hang

## Problem Analysis

The program hangs for ~30 seconds after entering the KeePass password. The user reports that decryption (KDF) cannot take that long, so the root cause must be elsewhere.

## Investigation Needed

1. Check KDF parameters of `default.vault` (Argon2 vs AES-KDF, memory/iteration params)
2. Time how long `pykeepass.PyKeePass()` takes with wrong password
3. Check if the hang is in `pykeepass.open()` or elsewhere in the call chain

## Proposed Tests (5 tests)

### Test 1: Timeout wrapper around pykeepass.open()
Add `kdf_timeout_sec` parameter to `_open_keepass_database()`, wrap with `ThreadPoolExecutor`.

### Test 2: AES-KDF fixture for fast integration tests
Create KDBX with AES-KDF instead of Argon2 for testing.

### Test 3: pytest-timeout safety net
Add `pytest-timeout` to dev dependencies.

### Test 4: Subprocess CLI test with wrong password
Run `uv run pyntara` with `PYNTARA_VAULT_PASSWORD=wrong`, verify exit code.

### Test 5: KDF parameter inspection test
Inspect vault file header to detect KDF type and warn if Argon2 with high parameters.