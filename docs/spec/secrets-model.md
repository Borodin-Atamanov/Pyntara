# Secrets model

## Vault files

The repository contains two KeePass vault files: default.vault and production.vault. Both are in git.

Two password files: default.password (in git, well-known test value) and production.password (in .gitignore, must never be committed).

KeePass database handling is done via a Python library.

## Password prompt

Deprecated: the interactive password prompt is not used and its development is stopped. The installer runs non-interactively. The production vault password is passed through the PYNTARA_VAULT_PASSWORD environment variable, entered by the user via read -s before sudo. Without a password the installer shows a countdown notice and falls back to default.vault. PYNTARA_VAULT_SOURCE (production or default) is optional: when omitted, the source is auto-detected from the password.

Historical summary of the removed prompt (for reference):
The user gets 3 attempts to enter the production vault password via bash read -s (VAULT_PASSWORD_TIMEOUT 333s timeout per attempt).
After 3 failed attempts, the system falls back to default.vault using default.password.
If user does not press any key within VAULT_PASSWORD_TIMEOUT seconds, fallback to default.vault immediately.

## Decrypted values

With a correct password:
the database is decrypted
some values become environment variables
some values are saved into internal machine configuration
some values are one-time-use and must only live in memory during execution

## Salts

The system uses salts:
default salt from GitHub
salt from KeePass, which overrides the default when present

Salt replacement must be reflected in logs.

## Password generation

Passwords are generated from salt + random hostname for:
root
user i
additional users j and k

Default password lengths:
root: 20 characters
regular user: 16 characters
