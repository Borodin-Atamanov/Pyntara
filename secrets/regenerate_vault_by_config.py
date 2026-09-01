#!/usr/bin/env python3
"""Create or update a KeePass vault from the [vault_structure] table.

The standalone maintenance script brings a KeePass database in line with
the vault structure described in config.toml, the single source of truth
(docs/spec/secrets-model.md). The mapping is one-to-one: the keys of a
[[vault_structure.entries]] table are the KeePass entry field names
(title, username, password, url, notes) and the values are the field
values. A key that is not a database field name is a config error: the
script stops before touching the vault. Future field names added to the
config (for example username) are applied as-is.

The vault password comes from the first available source in this order:
the PYNTARA_VAULT_PASSWORD environment variable, the file next to the
vault with the same name and the .password extension, and an interactive
prompt (only when stdin is a terminal). The script never writes password
files.

Modes:
- the vault file is absent or empty: create the vault from the config;
- --overwrite is given: recreate the vault from the config;
- otherwise: open the vault with the password and add the entries that
  are missing from the root group, keeping every existing entry.

Every write is atomic: the database is saved to a temporary file in the
same directory, verified by opening it with the password, and only then
moved into place. Exit codes: 0 success or no-op, 1 any error, 2 invalid
usage. With no arguments the script prints its usage help.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path

# The script lives in secrets/, so the repository root is one level up and
# the project virtualenv interpreter sits at its well-known location.
REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

# The script needs pykeepass, which is installed into the project
# virtualenv, not into the system python. When the script is invoked
# directly (./regenerate_vault_by_config.py), the kernel starts the system
# python3 and the import fails; the script then re-executes itself with the
# venv interpreter, whose path is deterministic. When pykeepass is still
# missing (dependencies not installed), the error below tells how to run it.
try:
    from pykeepass import PyKeePass, create_database
    from pykeepass.exceptions import CredentialsError
except ModuleNotFoundError:
    if __name__ == "__main__":
        if os.environ.get("PYNTARA_REEXEC") != "1" and VENV_PYTHON.is_file():
            os.environ["PYNTARA_REEXEC"] = "1"
            os.execv(
                str(VENV_PYTHON),
                [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            )
        print(
            "pykeepass is not available: install the project dependencies "
            "(uv sync) and run the script with the project interpreter, "
            f"for example {VENV_PYTHON} secrets/regenerate_vault_by_config.py",
            file=sys.stderr,
        )
        sys.exit(1)
    raise

# The joined config text and its ConfigError come from the shared loader,
# the same single source the engine uses; the script never re-implements
# the config reading.
from pyntara.config import ConfigError
from pyntara.config.loader import render_config_source
from pyntara.utils import proquint_encode

# KeePass entry fields that a [vault_structure] entry may name. The config
# names must equal the database field names one-to-one, no mapping; any
# other key is a config error. url is deliberately absent: per-entry
# values that are not structure (url, password) are maintained directly in
# the vault databases, not in the config.
VAULT_FIELD_NAMES: tuple[str, ...] = ("title", "username", "password", "notes")

# KeePass entry fields that a group seed entry may name. Seed entries are
# the default content of a data group, filled in when the script creates
# the group, so a fresh vault mirrors the structure before the real data
# is maintained directly in the database. url is a data value, allowed
# here because the seed carries it into the database, unlike the
# [vault_structure] entries whose url is rejected.
SEED_FIELD_NAMES: tuple[str, ...] = ("title", "url", "notes")

# The fields a [vault_structure] group may name: the group identity, the
# explanatory notes and the optional seed_entries array.
GROUP_FIELD_NAMES: tuple[str, ...] = VAULT_FIELD_NAMES + ("seed_entries",)

# The optional generated_password field of an entry, a generation
# instruction rather than a database field: "proquint-N" asks the script
# to generate a random password of N proquint words joined by dashes when
# it creates the entry, so the production secret is never copied into
# another vault.
GENERATED_PASSWORD_FIELD = "generated_password"
GENERATED_PASSWORD_RE = re.compile(r"^proquint-([1-9][0-9]*)$")

CONFIG_PATH = REPO_ROOT / "config"
EXIT_OK = 0
EXIT_ERROR = 1


class ScriptError(RuntimeError):
    """Fatal problem with the config, the password or the vault file."""


def _build_parser() -> argparse.ArgumentParser:
    """Argument parser; the vault path is optional to allow bare help."""

    parser = argparse.ArgumentParser(
        prog="regenerate_vault_by_config.py",
        description=(
            "Create or update a KeePass vault from the [vault_structure] "
            "table of config.toml."
        ),
    )
    parser.add_argument(
        "vault_path",
        nargs="?",
        help="path to the KeePass vault file to create or update",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="recreate the vault from the config even when it exists",
    )
    return parser


def load_vault_entries(config_path: Path) -> list[dict[str, str]]:
    """Read and validate the [vault_structure] table of config.toml.

    Every entry is returned as a dict whose keys are the configured field
    names and whose values are the field values. Unknown field names, a
    missing or empty title, duplicate titles and non-string values are
    config errors, reported with the offending entry.
    """

    if not config_path.exists():
        raise ScriptError(f"config file not found: {config_path}")
    try:
        data = tomllib.loads(render_config_source(config_path))
    except ConfigError as exc:
        raise ScriptError(str(exc)) from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ScriptError(f"cannot read config file {config_path}: {exc}") from exc
    table = data.get("vault_structure")
    if not isinstance(table, dict):
        raise ScriptError("[vault_structure] section is missing or not a table")
    entries_raw = table.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ScriptError(
            "[vault_structure] entries must be a non-empty array of tables"
        )
    entries: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for index, entry_raw in enumerate(entries_raw):
        if not isinstance(entry_raw, dict):
            raise ScriptError(f"[vault_structure] entry {index + 1} must be a table")
        unknown = sorted(
            name for name in entry_raw if name not in VAULT_FIELD_NAMES + (GENERATED_PASSWORD_FIELD,)
        )
        if unknown:
            raise ScriptError(
                f"[vault_structure] entry {index + 1} names unknown field(s) "
                f"{', '.join(unknown)}; expected one of "
                f"{', '.join(VAULT_FIELD_NAMES + (GENERATED_PASSWORD_FIELD,))}"
            )
        title = entry_raw.get("title")
        if not isinstance(title, str) or not title:
            raise ScriptError(
                f"[vault_structure] entry {index + 1}: title must be a "
                "non-empty string"
            )
        if title in seen_titles:
            raise ScriptError(f"[vault_structure] duplicate entry title: {title}")
        seen_titles.add(title)
        fields: dict[str, str] = {}
        for name in VAULT_FIELD_NAMES + (GENERATED_PASSWORD_FIELD,):
            value = entry_raw.get(name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ScriptError(
                    f"[vault_structure] entry {title}: field {name} must be "
                    "a string"
                )
            fields[name] = value
        generated = fields.get(GENERATED_PASSWORD_FIELD)
        if generated is not None and not GENERATED_PASSWORD_RE.match(generated):
            raise ScriptError(
                f"[vault_structure] entry {title}: generated_password must "
                "match 'proquint-N' with a positive word count"
            )
        if generated is not None and "password" in fields:
            raise ScriptError(
                f"[vault_structure] entry {title}: cannot set both password "
                "and generated_password"
            )
        entries.append(fields)
    return entries


def load_vault_groups(config_path: Path) -> list[dict[str, object]]:
    """Read the [vault_structure] groups array of config.toml.

    A group is a table with a unique non-empty title and a non-empty notes
    field, validated the same way as an entry, plus an optional
    seed_entries array: the default content the script fills into the
    group when it creates it, so a fresh vault mirrors the structure. The
    groups describe data subgroups (NextDNS accounts, port-forwarding
    server addresses) that the tooling fills with seed entries on creation
    and never edits afterwards.
    """

    if not config_path.exists():
        raise ScriptError(f"config file not found: {config_path}")
    try:
        data = tomllib.loads(render_config_source(config_path))
    except ConfigError as exc:
        raise ScriptError(str(exc)) from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ScriptError(f"cannot read config file {config_path}: {exc}") from exc
    table = data.get("vault_structure")
    if not isinstance(table, dict):
        raise ScriptError("[vault_structure] section is missing or not a table")
    groups_raw = table.get("groups")
    if groups_raw is None:
        return []
    if not isinstance(groups_raw, list):
        raise ScriptError("[vault_structure] groups must be an array of tables")
    groups: list[dict[str, object]] = []
    seen_titles: set[str] = set()
    for index, group_raw in enumerate(groups_raw):
        if not isinstance(group_raw, dict):
            raise ScriptError(f"[vault_structure] group {index + 1} must be a table")
        unknown = sorted(
            name for name in group_raw if name not in GROUP_FIELD_NAMES
        )
        if unknown:
            raise ScriptError(
                f"[vault_structure] group {index + 1} names unknown field(s) "
                f"{', '.join(unknown)}; expected one of "
                f"{', '.join(GROUP_FIELD_NAMES)}"
            )
        title = group_raw.get("title")
        if not isinstance(title, str) or not title:
            raise ScriptError(
                f"[vault_structure] group {index + 1}: title must be a "
                "non-empty string"
            )
        if title in seen_titles:
            raise ScriptError(f"[vault_structure] duplicate group title: {title}")
        seen_titles.add(title)
        notes = group_raw.get("notes")
        if not isinstance(notes, str) or not notes:
            raise ScriptError(
                f"[vault_structure] group {title}: notes must be a non-empty string"
            )
        groups.append(
            {
                "title": title,
                "notes": notes,
                "seed_entries": _load_group_seed_entries(group_raw, title),
            }
        )
    return groups


def _load_group_seed_entries(
    group_raw: dict[object, object], group_title: str
) -> list[dict[str, str]]:
    """The validated seed_entries array of a group, or an empty list.

    Each seed entry is a table with a unique non-empty title and optional
    url and notes fields. The url field carries the data value (for
    example the port-forwarding server address) into the freshly created
    group, so a new vault mirrors the structure before the real data is
    maintained directly in the database.
    """

    raw = group_raw.get("seed_entries")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ScriptError(
            f"[vault_structure] group {group_title}: seed_entries must be "
            "an array of tables"
        )
    seed_entries: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for index, seed_raw in enumerate(raw):
        if not isinstance(seed_raw, dict):
            raise ScriptError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{index + 1} must be a table"
            )
        unknown = sorted(
            name for name in seed_raw if name not in SEED_FIELD_NAMES
        )
        if unknown:
            raise ScriptError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{index + 1} names unknown field(s) {', '.join(unknown)}; "
                f"expected one of {', '.join(SEED_FIELD_NAMES)}"
            )
        seed_title = seed_raw.get("title")
        if not isinstance(seed_title, str) or not seed_title:
            raise ScriptError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{index + 1}: title must be a non-empty string"
            )
        if seed_title in seen_titles:
            raise ScriptError(
                f"[vault_structure] group {group_title}: duplicate seed "
                f"entry title: {seed_title}"
            )
        seen_titles.add(seed_title)
        url = seed_raw.get("url")
        if url is not None and not isinstance(url, str):
            raise ScriptError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{seed_title}: url must be a string"
            )
        seed_notes = seed_raw.get("notes")
        if seed_notes is not None and not isinstance(seed_notes, str):
            raise ScriptError(
                f"[vault_structure] group {group_title}: seed entry "
                f"{seed_title}: notes must be a string"
            )
        seed_entries.append(
            {"title": seed_title, "url": url or "", "notes": seed_notes or ""}
        )
    return seed_entries


def resolve_password(vault_path: Path, environ: Mapping[str, str]) -> str | None:
    """The vault password from the environment, the .password file or a prompt.

    The environment variable wins; the password file next to the vault is
    read and trimmed of surrounding whitespace; the interactive prompt is
    used only when stdin is a terminal. Returns None when no source yields
    a non-empty password. An existing but unreadable password file is a
    fatal error, per the script contract.
    """

    env_password = environ.get("PYNTARA_VAULT_PASSWORD")
    if env_password is not None:
        stripped = env_password.strip()
        if stripped:
            print("password: using the PYNTARA_VAULT_PASSWORD environment variable")
            return stripped
        print("password: PYNTARA_VAULT_PASSWORD is empty, trying the password file")
    password_file = vault_path.with_suffix(".password")
    if password_file.exists():
        print(f"password: reading {password_file}")
        try:
            file_password = password_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ScriptError(
                f"cannot read password file {password_file}: {exc}"
            ) from exc
        if file_password:
            return file_password
        print("password: the password file is empty, trying the prompt")
    else:
        print(f"password: no password file {password_file}")
    if not sys.stdin.isatty():
        print("password: stdin is not a terminal, cannot prompt")
        return None
    try:
        prompted = getpass.getpass("vault password: ")
    except (EOFError, KeyboardInterrupt):
        return None
    prompted = prompted.strip()
    if not prompted:
        return None
    return prompted


def _temp_path(vault_path: Path) -> Path:
    """A unique temporary file next to the vault for the atomic write."""

    vault_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f"{vault_path.name}.", suffix=".tmp", dir=str(vault_path.parent)
    )
    os.close(descriptor)
    return Path(name)


def _save_and_swap(
    kp: PyKeePass, tmp_path: Path, vault_path: Path, password: str
) -> None:
    """Save to the temporary file, verify it opens, then move it into place.

    The verification opens the written file with the password before the
    move, so a corrupt write never replaces the existing vault.
    """

    kp.save(filename=str(tmp_path))
    try:
        PyKeePass(str(tmp_path), password=password)
    except Exception as exc:  # noqa: BLE001 - any open failure rejects the write
        raise ScriptError(
            f"verification failed: the written vault does not open: {exc}"
        ) from exc
    os.replace(tmp_path, vault_path)
    print(f"saved: {vault_path} opens with the provided password")


def _generated_password(fields: dict[str, str]) -> str | None:
    """A freshly generated password for the entry, or None.

    An entry with generated_password "proquint-N" receives a random
    password of N proquint words joined by dashes, so the production
    secret is never copied into another vault. The word count is taken
    from the validated spec.
    """

    spec = fields.get(GENERATED_PASSWORD_FIELD)
    if spec is None:
        return None
    match = GENERATED_PASSWORD_RE.match(spec)
    if match is None:
        raise ScriptError(
            f"entry {fields['title']}: invalid generated_password spec {spec!r}"
        )
    word_count = int(match.group(1))
    return proquint_encode(os.urandom(2 * word_count))


def _add_entry(kp: PyKeePass, fields: dict[str, str]) -> None:
    """Add one entry to the root group from the configured fields.

    The configured field names are the add_entry parameters, so the
    one-to-one mapping is applied verbatim; a generated_password spec is
    replaced by a freshly generated password, and absent optional fields
    stay empty or None.
    """

    kp.add_entry(
        kp.root_group,
        title=fields["title"],
        username=fields.get("username", ""),
        password=_generated_password(fields) or fields.get("password", ""),
        url=fields.get("url"),
        notes=fields.get("notes"),
    )


def _add_group(kp: PyKeePass, group: dict[str, object]) -> None:
    """Create one data subgroup named by the config.

    The group is created under the root with the configured title and
    notes. Its configured seed entries are created inside it, so a freshly
    created vault mirrors the structure; after creation the group is never
    edited, so a regeneration cannot lose or invent accounts.
    """

    new_group = kp.add_group(kp.root_group, group["title"], notes=group["notes"])
    for seed in group.get("seed_entries", []):
        kp.add_entry(
            new_group,
            title=seed["title"],
            username="",
            password="",
            url=seed.get("url") or None,
            notes=seed.get("notes") or None,
        )


def _ensure_group(
    kp: PyKeePass, group: dict[str, object], vault_path: Path
) -> bool:
    """Create the configured subgroup when it is missing; True when created.

    The title is the identity of the group, so an existing group with the
    same title is kept untouched, including its entries. A newly created
    group receives its configured seed entries.
    """

    title = group["title"]
    if kp.find_groups(name=title, first=True) is not None:
        print(f"group {title!r}: present, keeping")
        return False
    _add_group(kp, group)
    print(f"group {title!r}: created")
    return True


def _report_empty_entries(entries: list[dict[str, str]]) -> None:
    """List the entries without a password value; they need manual filling.

    Entries with a generated_password spec are skipped: they receive a
    generated password on creation and are never empty.
    """

    empty = [
        fields["title"]
        for fields in entries
        if not fields.get("password") and not fields.get(GENERATED_PASSWORD_FIELD)
    ]
    if empty:
        print(
            "note: entries with an empty password value, fill them in "
            f"KeePass before use: {', '.join(empty)}"
        )


def _recreate(
    vault_path: Path,
    entries: list[dict[str, str]],
    groups: list[dict[str, str]],
    password: str,
) -> int:
    """Create or recreate the vault from the config entries.

    The root entries come first, then the configured subgroups are created
    empty, because the accounts inside are data maintained directly in the
    vault databases.
    """

    tmp_path = _temp_path(vault_path)
    try:
        kp = create_database(str(tmp_path), password=password)
        for fields in entries:
            print(f"adding entry: {fields['title']}")
            _add_entry(kp, fields)
        for group in groups:
            _add_group(kp, group)
        _save_and_swap(kp, tmp_path, vault_path, password)
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"created: {vault_path} with {len(entries)} entries")
    _report_empty_entries(entries)
    return EXIT_OK


def _update(
    vault_path: Path,
    entries: list[dict[str, str]],
    groups: list[dict[str, str]],
    password: str,
) -> int:
    """Add the entries and groups missing; keep everything else.

    Entries missing from the root group are added, the configured
    subgroups are created when absent. The entries inside an existing
    subgroup are never touched, so the accounts survive the update.
    """

    try:
        kp = PyKeePass(str(vault_path), password=password)
    except CredentialsError:
        raise ScriptError(
            f"cannot open vault {vault_path}: the password does not match "
            "(use --overwrite to recreate the vault from the config)"
        ) from None
    except Exception as exc:  # noqa: BLE001 - any open failure is fatal
        raise ScriptError(
            f"cannot open vault {vault_path}: {exc} (use --overwrite to "
            "recreate the vault from the config)"
        ) from exc
    missing: list[dict[str, str]] = []
    for fields in entries:
        title = fields["title"]
        if (
            kp.find_entries(
                title=title, group=kp.root_group, recursive=False, first=True
            )
            is not None
        ):
            print(f"entry {title!r}: present in the root group, keeping")
            continue
        if kp.find_entries(title=title, recursive=True, first=True) is not None:
            print(
                f"entry {title!r}: exists in a subgroup, adding to the root "
                "group per the flat structure"
            )
        missing.append(fields)
    for group in groups:
        _ensure_group(kp, group, vault_path)
    if not missing:
        print("state: the vault already matches the structure, no changes")
        return EXIT_OK
    tmp_path = _temp_path(vault_path)
    try:
        for fields in missing:
            print(f"adding entry: {fields['title']}")
            _add_entry(kp, fields)
        _save_and_swap(kp, tmp_path, vault_path, password)
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"updated: added {len(missing)} entries to {vault_path}")
    _report_empty_entries(entries)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Script entry point; returns the process exit code."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.vault_path is None:
        parser.print_help()
        return EXIT_OK
    vault_path = Path(args.vault_path)
    print(f"vault: {vault_path}")
    try:
        entries = load_vault_entries(CONFIG_PATH)
        groups = load_vault_groups(CONFIG_PATH)
        print(
            f"config: {CONFIG_PATH}, {len(entries)} entries, "
            f"{len(groups)} groups in [vault_structure]"
        )
        password = resolve_password(vault_path, os.environ)
        if password is None:
            raise ScriptError(
                "no password available: the environment variable, the "
                "password file and the prompt all failed"
            )
        if not vault_path.exists():
            print("state: the vault file is absent, recreating from the config")
            return _recreate(vault_path, entries, groups, password)
        if vault_path.stat().st_size == 0:
            print("state: the vault file is empty, recreating from the config")
            return _recreate(vault_path, entries, groups, password)
        if args.overwrite:
            print("state: --overwrite given, recreating from the config")
            return _recreate(vault_path, entries, groups, password)
        print("state: the vault file exists, updating missing entries")
        return _update(vault_path, entries, groups, password)
    except ScriptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
