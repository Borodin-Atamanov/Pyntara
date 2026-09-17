"""Values of the local_vault_setup task.

The task copies the first source vault that the run password decrypts,
re-encrypts it with the local vault password and stores the vault and the
password in fixed system locations, so services that start after install can
decrypt the vault without user input. The source vault paths are relative to the
clone root and live in the shared module common, because nextdns_setup_system_wide
resolves the same two paths; the target paths are absolute and fixed by
docs/spec/secrets-model.md.
"""

from __future__ import annotations

from pathlib import Path

# Target path of the runtime secret vault, with SECRETS_DIR_MODE on its
# directory and LOCAL_VAULT_FILE_MODE on the file.
LOCAL_VAULT_PATH: Path = Path("/var/lib/pyntara/secrets/pyntara.vault")

# Target path of the plain root-only vault password file, with PASS_DIR_MODE on
# its directory and PASS_FILE_MODE on the file.
PASS_FILE_PATH: Path = Path("/etc/pyntara/pass")

# Title of the source vault entry that carries the future local vault password.
# The entry must exist in the vault structure of pyntara.values.vault_structure;
# a guard in tests/test_values.py refuses a title that is not listed there.
VAULT_PASSWORD_ENTRY_TITLE: str = "pyntara_local_vault_password"

# File modes of the runtime secret storage (docs/spec/secrets-model.md): the
# secrets directory, the runtime vault, the password directory and the password
# file.
SECRETS_DIR_MODE: int = 0o700
LOCAL_VAULT_FILE_MODE: int = 0o640
PASS_DIR_MODE: int = 0o700
PASS_FILE_MODE: int = 0o400

# Mode that makes an existing read-only password file writable by its owner
# before a force rewrite; PASS_FILE_MODE is restored right after the write.
PASS_FILE_WRITABLE_MODE: int = 0o600

# Syslog priority used when journaling a serious vault failure, 0 to 7.
ERROR_PRIORITY: int = 3

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "LOCAL_VAULT_PATH",
    "PASS_FILE_PATH",
    "VAULT_PASSWORD_ENTRY_TITLE",
    "SECRETS_DIR_MODE",
    "LOCAL_VAULT_FILE_MODE",
    "PASS_DIR_MODE",
    "PASS_FILE_MODE",
    "PASS_FILE_WRITABLE_MODE",
    "ERROR_PRIORITY",
)
