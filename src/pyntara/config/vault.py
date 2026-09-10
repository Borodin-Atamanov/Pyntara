"""[vault_structure] and [local_vault_setup] tables.

The local vault password entry title must name an entry of the vault
structure, so the two tables live in one module; the cross-check itself
happens in loader.py where the full Config is assembled.
"""


from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

GENERATED_PASSWORD_RE = re.compile(r"^proquint-[1-9][0-9]*$")


@dataclass(frozen=True)
class VaultEntry:
    """One entry of the [vault_structure] table.

    title names the KeePass entry; notes carries the explanatory text that
    the regeneration tooling stores in the notes field of the entry.
    generated_password, when set, asks the regeneration tooling to
    generate the password when it creates the entry, in the format
    "proquint-N".
    """

    title: str
    notes: str
    generated_password: str | None = None


@dataclass(frozen=True)
class VaultGroupSeed:
    """One seed entry of a [vault_structure] group.

    title names the KeePass entry the regeneration tooling creates inside
    the group when it creates the group; url carries the data value (for
    example a port-forwarding server address) and notes carries the
    explanatory text. Seed entries are the default content of a data
    group, so a freshly created vault mirrors the structure before the
    real data is maintained directly in the database.
    """

    title: str
    url: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class VaultGroup:
    """One data subgroup of the [vault_structure] table.

    title names the KeePass group, notes explains what it carries.
    seed_entries are the entries the regeneration tooling creates inside
    the group when it creates the group, so a fresh vault starts as a
    faithful mirror; once the group exists, the tooling never touches its
    entries.
    """

    title: str
    notes: str
    seed_entries: tuple[VaultGroupSeed, ...] = ()


@dataclass(frozen=True)
class VaultStructureConfig:
    """KeePass vault layout described in the [vault_structure] table.

    The table is the single source of truth for the vault structure
    (docs/spec/secrets-model.md): the structure is flat, every entry lives
    in the root group and is identified by its unique title; notes
    explains what the entry carries and who consumes it. The optional
    groups are data subgroups (NextDNS accounts, port-forwarding server
    addresses): the regeneration tooling creates them, fills each with its
    configured seed entries on creation, and never touches the entries
    afterwards.
    """

    entries: tuple[VaultEntry, ...]
    groups: tuple[VaultGroup, ...] = ()


@dataclass(frozen=True)
class LocalVaultSetupConfig:
    """Runtime secret vault parameters for the local_vault_setup task.

    source_vault_production and source_vault_default are repository-root
    relative paths to the KeePass databases whose copy becomes the runtime
    vault; local_vault_path and pass_file_path are the absolute target
    locations fixed by docs/spec/secrets-model.md; vault_password_entry_title
    names the source vault entry (from the [vault_structure] table) that
    carries the future local vault password.
    """

    source_vault_production: Path
    source_vault_default: Path
    local_vault_path: Path
    pass_file_path: Path
    vault_password_entry_title: str
    secrets_dir_mode: int
    local_vault_file_mode: int
    pass_dir_mode: int
    pass_file_mode: int
    error_priority: int
