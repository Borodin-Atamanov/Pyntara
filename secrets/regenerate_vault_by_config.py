#!/usr/bin/env python3
"""Create or update a KeePass vault from the vault structure values.

The standalone maintenance script brings a KeePass database in line with the
vault structure of pyntara.values.vault_structure, the single source of truth
(docs/spec/secrets-model.md). The mapping is one-to-one: the fields of an entry
record are the KeePass entry field names (title, username, password, url, notes)
and the record values are the field values. Future field names added to the
structure are applied as-is.

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

# The vault structure comes from the values package, the same single source the
# engine and the values guards read; the script never keeps a copy of it.
from pyntara.utils import proquint_encode
from pyntara.values import vault_structure as values

# The optional generated_password field of an entry, a generation
# instruction rather than a database field: "proquint-N" asks the script
# to generate a random password of N proquint words joined by dashes when
# it creates the entry, so the production secret is never copied into
# another vault.
GENERATED_PASSWORD_FIELD = "generated_password"
GENERATED_PASSWORD_RE = re.compile(r"^proquint-([1-9][0-9]*)$")

EXIT_OK = 0
EXIT_ERROR = 1


class ScriptError(RuntimeError):
    """Fatal problem with the config, the password or the vault file."""


def _build_parser() -> argparse.ArgumentParser:
    """Argument parser; the vault path is optional to allow bare help."""

    parser = argparse.ArgumentParser(
        prog="regenerate_vault_by_config.py",
        description=(
            "Create or update a KeePass vault from the vault structure "
            "values of pyntara.values.vault_structure."
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


def load_vault_entries() -> list[dict[str, str]]:
    """The vault entries as the field dictionaries the vault is written from.

    The single source is pyntara.values.vault_structure, the module the values
    guards read; the field names are the KeePass field names, so the mapping
    stays one-to-one. The record type already guarantees that a title and a note
    are texts, so what is left to refuse here is an empty or repeated title,
    which would make a database entry ambiguous, and a generated_password that
    does not match its format.
    """

    entries: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for index, record in enumerate(values.ENTRIES):
        if not record.title:
            raise ScriptError(
                f"vault structure entry {index + 1}: title is empty"
            )
        if record.title in seen_titles:
            raise ScriptError(
                f"vault structure duplicate entry title: {record.title}"
            )
        seen_titles.add(record.title)
        fields: dict[str, str] = {
            "title": record.title,
            "notes": record.notes,
        }
        if record.generated_password is not None:
            if not GENERATED_PASSWORD_RE.match(record.generated_password):
                raise ScriptError(
                    f"vault structure entry {record.title}: "
                    "generated_password must match 'proquint-N' with a "
                    "positive word count"
                )
            fields[GENERATED_PASSWORD_FIELD] = record.generated_password
        entries.append(fields)
    return entries


def load_vault_groups() -> list[dict[str, object]]:
    """The data subgroups of the vault structure, ready for the vault writer.

    The single source is pyntara.values.vault_structure, like the entries. A
    group carries its notes and the seed entries the script fills into the
    freshly created group; an empty or repeated title is refused, because it
    would make the group ambiguous.
    """

    groups: list[dict[str, object]] = []
    seen_titles: set[str] = set()
    for index, record in enumerate(values.GROUPS):
        if not record.title:
            raise ScriptError(f"vault structure group {index + 1}: title is empty")
        if record.title in seen_titles:
            raise ScriptError(
                f"vault structure duplicate group title: {record.title}"
            )
        seen_titles.add(record.title)
        if not record.notes:
            raise ScriptError(
                f"vault structure group {record.title}: notes are empty"
            )
        groups.append(
            {
                "title": record.title,
                "notes": record.notes,
                "seed_entries": _load_group_seed_entries(record),
            }
        )
    return groups


def _load_group_seed_entries(group: values.VaultGroup) -> list[dict[str, str]]:
    """The seed entries of one group, ready for the vault writer.

    A seed entry carries the data value (for example the port-forwarding server
    address) into the freshly created group, so a new vault mirrors the
    structure before the real data is maintained directly in the database. An
    empty or repeated title is refused here too.
    """

    seed_entries: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for index, seed in enumerate(group.seed_entries):
        title = seed.title or ""
        if not title:
            raise ScriptError(
                f"vault structure group {group.title}: seed entry "
                f"{index + 1}: title is empty"
            )
        if title in seen_titles:
            raise ScriptError(
                f"vault structure group {group.title}: duplicate seed entry "
                f"title: {title}"
            )
        seen_titles.add(title)
        seed_entries.append(
            {
                "title": title,
                "url": seed.url or "",
                "notes": seed.notes or "",
            }
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
        entries = load_vault_entries()
        groups = load_vault_groups()
        print(
            f"structure: pyntara.values.vault_structure, {len(entries)} entries, "
            f"{len(groups)} groups"
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
