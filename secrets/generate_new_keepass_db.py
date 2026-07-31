#!/usr/bin/env python3
"""Create or gently update a KeePass database with Pyntara secret structure.

This script is intentionally standalone and user-invoked. It is not a task runner step.
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SELF_BOOTSTRAP_SENTINEL = "PYNTARA_KEEPASS_SELF_BOOTSTRAP"
DEFAULT_KEEPASS_VENV = Path.home() / ".cache" / "pyntara" / "venvs" / "keepass-tools"
DEFAULT_PIP_CACHE = Path.home() / ".cache" / "pyntara" / "pip"
PIP_INSTALL_TIMEOUT_SEC = 1200


def _venv_has_pykeepass(python_bin: Path) -> bool:
    """Return True when the target runtime can import pykeepass."""

    try:
        check_result = subprocess.run(
            [str(python_bin), "-c", "import pykeepass"],
            check=False,
            timeout=PIP_INSTALL_TIMEOUT_SEC,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return check_result.returncode == 0


def _ensure_pykeepass_venv() -> Path:
    """Install pykeepass into a user-level virtualenv and return its Python binary."""

    venv_dir = Path(
        os.environ.get("PYNTARA_KEEPASS_VENV_DIR", str(DEFAULT_KEEPASS_VENV))
    ).expanduser()
    python_bin = venv_dir / "bin" / "python3"
    pip_bin = venv_dir / "bin" / "pip"

    if not python_bin.exists():
        venv_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            timeout=PIP_INSTALL_TIMEOUT_SEC,
        )

    if _venv_has_pykeepass(python_bin):
        return python_bin

    pip_cache_dir = Path(
        os.environ.get("PIP_CACHE_DIR", str(DEFAULT_PIP_CACHE))
    ).expanduser()
    pip_cache_dir.mkdir(parents=True, exist_ok=True)
    install_env = dict(os.environ)
    install_env["PIP_CACHE_DIR"] = str(pip_cache_dir)
    subprocess.run(
        [
            str(pip_bin),
            "install",
            "--disable-pip-version-check",
            "--quiet",
            "pykeepass>=4.1.1",
        ],
        check=True,
        timeout=PIP_INSTALL_TIMEOUT_SEC,
        env=install_env,
    )
    return python_bin


def _bootstrap_and_reexec() -> None:
    """Ensure pykeepass is available in a local runtime and restart this script."""

    if os.environ.get(SELF_BOOTSTRAP_SENTINEL) == "1":
        raise SystemExit(
            "pykeepass is still unavailable after automatic setup."
        )

    python_bin = _ensure_pykeepass_venv()
    next_env = dict(os.environ)
    next_env[SELF_BOOTSTRAP_SENTINEL] = "1"
    os.execve(
        str(python_bin),
        [str(python_bin), str(Path(__file__).resolve()), *sys.argv[1:]],
        next_env,
    )


try:
    from pykeepass import PyKeePass, create_database
    from pykeepass.exceptions import CredentialsError, HeaderChecksumError
except ModuleNotFoundError as import_error:
    if shutil.which("uv"):
        raise SystemExit(
            "pykeepass is required. Install dependencies first, for example:\n"
            "  uv sync"
        ) from import_error
    _bootstrap_and_reexec()


# Bump this when the baseline structure changes in this file.
SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class EntrySpec:
    """Desired entry shape inside one group."""

    title: str
    username: str
    password: str
    url: str = ""
    notes: str = ""


# Required structure for both test and production vaults.
# Keeping it in code guarantees both files are generated from one source of truth.
SCHEMA: dict[tuple[str, ...], list[EntrySpec]] = {
    ("meta",): [
        EntrySpec(
            title="schema_version",
            username="pyntara",
            password=SCHEMA_VERSION,
            notes="Used by tooling to understand expected vault structure.",
        ),
    ],
    ("core",): [
        EntrySpec(
            title="salt",
            username="pyntara",
            password="",
            notes="Primary salt for deterministic password derivation.",
        ),
    ],
    ("telemetry", "telegram"): [
        EntrySpec(
            title="bot_token",
            username="telegram_bot",
            password="",
            notes="Telegram bot token used by telemetry sender.",
        ),
        EntrySpec(
            title="chat_id",
            username="telegram_chat",
            password="",
            notes="Telegram destination chat id.",
        ),
    ],
    ("telemetry", "gdrive"): [
        EntrySpec(
            title="service_account_json",
            username="google_drive",
            password="",
            notes="JSON credentials payload for Google Drive uploader.",
        ),
    ],
    ("network", "proxy_remote"): [
        EntrySpec(
            title="host",
            username="proxy",
            password="",
            notes="Remote proxy host.",
        ),
        EntrySpec(
            title="port",
            username="proxy",
            password="",
            notes="Remote proxy port.",
        ),
        EntrySpec(
            title="username",
            username="proxy",
            password="",
            notes="Remote proxy username.",
        ),
        EntrySpec(
            title="password",
            username="proxy",
            password="",
            notes="Remote proxy password.",
        ),
    ],
    ("network", "nextdns"): [
        EntrySpec(
            title="profile_dns",
            username="nextdns",
            password="",
            notes="Generated NextDNS endpoint for system DNS configuration.",
        ),
    ],
}


@dataclass(frozen=True, slots=True)
class ReconcileStats:
    """Counters for user-visible output."""

    groups_created: int
    entries_created: int
    entries_touched: int


def _prompt_password(*, confirm: bool) -> str:
    """Ask for database password without echoing text in terminal."""

    password = getpass.getpass("KeePass password: ")
    if password == "":
        raise ValueError("Password must not be empty.")

    if not confirm:
        return password

    repeat_password = getpass.getpass("Repeat password: ")
    if password != repeat_password:
        raise ValueError("Password confirmation does not match.")
    return password


def _open_existing_database(path: Path) -> tuple[PyKeePass, str]:
    """Open existing database and allow several attempts for password typing mistakes."""

    attempts_left = 3
    while attempts_left > 0:
        try:
            password = _prompt_password(confirm=False)
            return PyKeePass(str(path), password=password), password
        except CredentialsError:
            attempts_left -= 1
            print(f"Wrong password. Attempts left: {attempts_left}", file=sys.stderr)
        except HeaderChecksumError as checksum_error:
            raise SystemExit(f"File is not a KeePass database: {path}") from checksum_error
        except ValueError as value_error:
            attempts_left -= 1
            print(str(value_error), file=sys.stderr)

    raise SystemExit("Failed to open KeePass database: password attempts exhausted.")


def _create_new_database(path: Path) -> tuple[PyKeePass, str]:
    """Create a new KDBX file and reopen it through PyKeePass API."""

    password = _prompt_password(confirm=True)
    create_database(str(path), password=password)
    return PyKeePass(str(path), password=password), password


def _ensure_group(kp: PyKeePass, group_path: tuple[str, ...]) -> tuple[object, int]:
    """Ensure each group in path exists and return leaf group object."""

    current_group = kp.root_group
    created = 0
    for name in group_path:
        # We only search direct children to avoid grabbing same-named groups elsewhere.
        next_group = kp.find_groups(
            name=name, group=current_group, recursive=False, first=True
        )
        if next_group is None:
            next_group = kp.add_group(current_group, name)
            created += 1
        current_group = next_group
    return current_group, created


def _ensure_entry(kp: PyKeePass, group: object, spec: EntrySpec) -> tuple[int, int]:
    """Create missing entry or gently fill empty metadata fields on existing entry."""

    # Limit lookup to this group to preserve unrelated entries in other sections.
    existing = kp.find_entries(title=spec.title, group=group, recursive=False, first=True)
    if existing is None:
        kp.add_entry(
            group,
            title=spec.title,
            username=spec.username,
            password=spec.password,
            url=spec.url,
            notes=spec.notes,
        )
        return 1, 0

    touched = 0
    if existing.username == "" and spec.username != "":
        existing.username = spec.username
        touched += 1
    if existing.password == "" and spec.password != "":
        existing.password = spec.password
        touched += 1
    if existing.url == "" and spec.url != "":
        existing.url = spec.url
        touched += 1
    if (existing.notes or "") == "" and spec.notes != "":
        existing.notes = spec.notes
        touched += 1
    return 0, touched


def reconcile_schema(kp: PyKeePass) -> ReconcileStats:
    """Apply required schema while preserving existing user data whenever possible."""

    groups_created = 0
    entries_created = 0
    entries_touched = 0

    for group_path, entries in SCHEMA.items():
        group, created_groups = _ensure_group(kp, group_path)
        groups_created += created_groups
        for spec in entries:
            created_entries, touched_entries = _ensure_entry(kp, group, spec)
            entries_created += created_entries
            entries_touched += touched_entries

    kp.save()
    return ReconcileStats(
        groups_created=groups_created,
        entries_created=entries_created,
        entries_touched=entries_touched,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Create or update a KeePass database with Pyntara baseline structure.\n"
            "The password is always requested interactively."
        )
    )
    parser.add_argument(
        "database_file",
        help="Path to target KeePass .kdbx file (created if missing).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Script entrypoint."""

    args = parse_args(sys.argv[1:] if argv is None else argv)
    database_path = Path(args.database_file).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    if database_path.exists():
        kp, _password = _open_existing_database(database_path)
        mode = "updated"
    else:
        kp, _password = _create_new_database(database_path)
        mode = "created"

    stats = reconcile_schema(kp)
    print(f"Database {mode}: {database_path}")
    print(f"Groups created: {stats.groups_created}")
    print(f"Entries created: {stats.entries_created}")
    print(f"Entries gently updated: {stats.entries_touched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
