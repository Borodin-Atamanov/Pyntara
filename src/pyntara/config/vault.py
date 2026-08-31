"""[vault_structure] and [local_vault_setup] tables.

The local vault password entry title must name an entry of the vault
structure, so the two tables live in one module; the cross-check itself
happens in loader.py where the full Config is assembled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ._fields import ConfigError, _int_field, _octal_mode_field

# Value format of the optional generated_password field of a vault
# structure entry: "proquint-N" asks the regeneration tooling to generate
# a random password of N proquint words joined by dashes when it creates
# the entry, so the production secret is never copied into another vault.
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
class VaultStructureConfig:
    """KeePass vault layout described in the [vault_structure] table.

    The table is the single source of truth for the vault structure
    (docs/spec/secrets-model.md): the structure is flat, every entry lives
    in the root group and is identified by its unique title; notes
    explains what the entry carries and who consumes it. The optional
    groups are data subgroups (NextDNS accounts): the regeneration tooling
    creates them but never fills or deletes their entries.
    """

    entries: tuple[VaultEntry, ...]
    groups: tuple[VaultEntry, ...] = ()


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


def _vault_structure_table(raw: object) -> VaultStructureConfig:
    """Validate the [vault_structure] table and build VaultStructureConfig.

    The section is mandatory and non-empty; every entry is a table with a
    unique non-empty title and a non-empty notes field. The structure is
    flat by contract, so the parser reads entries directly from the table
    and rejects unknown field names: url and any other per-entry value
    live in the vault database, not in the config. The optional groups
    array describes the data subgroups (NextDNS accounts): a group is a
    table with a unique non-empty title and notes, validated the same way
    as an entry.
    """

    if not isinstance(raw, dict):
        raise ConfigError("[vault_structure] section is missing or not a table")
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ConfigError(
            "[vault_structure] entries must be a non-empty array of tables"
        )
    entries: list[VaultEntry] = []
    seen_titles: set[str] = set()
    for index, entry_raw in enumerate(entries_raw):
        if not isinstance(entry_raw, dict):
            raise ConfigError("[vault_structure] entries must be tables")
        unknown = sorted(
            name for name in entry_raw if name not in ("title", "notes", "generated_password")
        )
        if unknown:
            raise ConfigError(
                f"[vault_structure] entry {index + 1} names unknown field(s) "
                f"{', '.join(unknown)}; expected title, notes, generated_password"
            )
        title = entry_raw.get("title")
        if not isinstance(title, str) or not title:
            raise ConfigError(
                "[vault_structure] entry title must be a non-empty string"
            )
        if title in seen_titles:
            raise ConfigError(f"[vault_structure] duplicate entry title: {title}")
        seen_titles.add(title)
        notes = entry_raw.get("notes")
        if not isinstance(notes, str) or not notes:
            raise ConfigError(
                f"[vault_structure] entry {title}: notes must be a non-empty string"
            )
        generated_password = entry_raw.get("generated_password")
        if generated_password is not None and not isinstance(
            generated_password, str
        ):
            raise ConfigError(
                f"[vault_structure] entry {title}: generated_password must be "
                "a string like 'proquint-7'"
            )
        if generated_password is not None and not GENERATED_PASSWORD_RE.match(
            generated_password
        ):
            raise ConfigError(
                f"[vault_structure] entry {title}: generated_password must "
                "match 'proquint-N' with a positive word count"
            )
        entries.append(
            VaultEntry(
                title=title,
                notes=notes,
                generated_password=generated_password,
            )
        )
    groups_raw = raw.get("groups")
    if groups_raw is None:
        groups: tuple[VaultEntry, ...] = ()
    else:
        if not isinstance(groups_raw, list):
            raise ConfigError("[vault_structure] groups must be an array of tables")
        groups_list: list[VaultEntry] = []
        for index, group_raw in enumerate(groups_raw):
            if not isinstance(group_raw, dict):
                raise ConfigError("[vault_structure] groups must be tables")
            unknown = sorted(
                name for name in group_raw if name not in ("title", "notes")
            )
            if unknown:
                raise ConfigError(
                    f"[vault_structure] group {index + 1} names unknown field(s) "
                    f"{', '.join(unknown)}; expected title, notes"
                )
            title = group_raw.get("title")
            if not isinstance(title, str) or not title:
                raise ConfigError(
                    "[vault_structure] group title must be a non-empty string"
                )
            if title in seen_titles:
                raise ConfigError(
                    f"[vault_structure] duplicate group title: {title}"
                )
            seen_titles.add(title)
            notes = group_raw.get("notes")
            if not isinstance(notes, str) or not notes:
                raise ConfigError(
                    f"[vault_structure] group {title}: notes must be a "
                    "non-empty string"
                )
            groups_list.append(VaultEntry(title=title, notes=notes))
        groups = tuple(groups_list)
    return VaultStructureConfig(entries=tuple(entries), groups=groups)


def _local_vault_setup_table(raw: object) -> LocalVaultSetupConfig:
    """Validate the [local_vault_setup] table and build the config.

    Source vault paths and the entry title are non-empty strings; the
    source paths are repository-root relative, the target paths absolute
    (the fixed locations from docs/spec/secrets-model.md).
    """

    if not isinstance(raw, dict):
        raise ConfigError("[local_vault_setup] section is missing or not a table")
    source_vault_production = raw.get("source_vault_production")
    if not isinstance(source_vault_production, str) or not source_vault_production:
        raise ConfigError(
            "local_vault_setup.source_vault_production must be a non-empty string"
        )
    source_vault_default = raw.get("source_vault_default")
    if not isinstance(source_vault_default, str) or not source_vault_default:
        raise ConfigError(
            "local_vault_setup.source_vault_default must be a non-empty string"
        )
    local_vault_path = raw.get("local_vault_path")
    if not isinstance(local_vault_path, str) or not local_vault_path:
        raise ConfigError(
            "local_vault_setup.local_vault_path must be a non-empty string"
        )
    pass_file_path = raw.get("pass_file_path")
    if not isinstance(pass_file_path, str) or not pass_file_path:
        raise ConfigError(
            "local_vault_setup.pass_file_path must be a non-empty string"
        )
    vault_password_entry_title = raw.get("vault_password_entry_title")
    if not isinstance(vault_password_entry_title, str) or not vault_password_entry_title:
        raise ConfigError(
            "local_vault_setup.vault_password_entry_title must be a non-empty string"
        )

    def _file_mode_field(name: str) -> int:
        """Parse one octal file mode string like "0700" into an int."""

        return _octal_mode_field(raw.get(name), f"local_vault_setup.{name}")

    error_priority = _int_field(
        raw.get("error_priority"), "local_vault_setup.error_priority"
    )
    if not 0 <= error_priority <= 7:
        raise ConfigError(
            "local_vault_setup.error_priority must be between 0 and 7"
        )
    return LocalVaultSetupConfig(
        source_vault_production=Path(source_vault_production),
        source_vault_default=Path(source_vault_default),
        local_vault_path=Path(local_vault_path),
        pass_file_path=Path(pass_file_path),
        vault_password_entry_title=vault_password_entry_title,
        secrets_dir_mode=_file_mode_field("secrets_dir_mode"),
        local_vault_file_mode=_file_mode_field("local_vault_file_mode"),
        pass_dir_mode=_file_mode_field("pass_dir_mode"),
        pass_file_mode=_file_mode_field("pass_file_mode"),
        error_priority=error_priority,
    )
