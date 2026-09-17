"""Task local_vault_setup: create the runtime secret vault on the machine.

The runtime secret database at the configured local vault path and its password
file at the pass file path are created so services that start after install can
decrypt the vault without user input (docs/spec/secrets-model.md). The copy is
re-encrypted with the password from the vault password entry of the source
vault, so the source password never opens the runtime vault. The entry lives in
the root group of the vault, because the structure is flat. Every value comes
from pyntara.values.local_vault_setup, and the two source vault paths come from
the shared module common, because nextdns_setup_system_wide resolves the same
two paths. The source vault is not fixed: the production vault
is tried first, then the default vault, both with the password from
Context; when neither opens, the task journals a serious error at syslog
level 3 and fails without stopping the run. The task is idempotent:
without force it leaves an existing runtime vault and copies every
source entry missing from it, in the root group and in the subgroups, so
a vault created by an older run gains the entries and groups the
structure gained later; force mode rewrites the vault and the password
file. Passwords are written to files
trimmed of surrounding whitespace and strictly without a trailing newline.
"""

from __future__ import annotations

import os
from pathlib import Path

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import apply_owner
from pyntara.values import common as common_values
from pyntara.values import local_vault_setup as values
from pyntara.values import missing_value_names


def _resolve_source_vault(
    repo_root: Path, production: str, default: str
) -> tuple[Path, Path]:
    """Source vault paths resolved against the repository root.

    The source paths are relative to the repository root, so the clone can live
    anywhere on the machine (/var/cache/pyntara/repo in production, a temporary
    directory in tests); the root comes from the context and the two texts from
    the caller.
    """

    return (repo_root / production, repo_root / default)


def _open_source_vault(
    production_path: Path, default_path: Path, password: str | None
) -> tuple[PyKeePass, Path] | None:
    """Open the first source vault the password decrypts; None when neither.

    Production is tried first, then default. A CredentialsError means the
    password does not match that vault, so the next candidate is tried; a
    missing file or any other open failure is logged the same way, because
    the goal is to produce the runtime vault from whatever source is
    available.
    """

    for path in (production_path, default_path):
        if password is None:
            _log(f"cannot open source vault {path}: no password provided")
            continue
        try:
            kp = PyKeePass(str(path), password=password)
        except CredentialsError:
            _log(f"cannot open source vault {path}: password does not match")
            continue
        except Exception as exc:  # noqa: BLE001 - any open failure moves to the next vault
            _log(f"cannot open source vault {path}: {exc}")
            continue
        _log(f"source vault opened: {path}")
        return kp, path
    return None


def open_source_vault(
    repo_root: Path,
    production: str,
    default: str,
    password: str | None,
) -> tuple[PyKeePass, Path] | None:
    """Open the first source vault for a run password, or None.

    The public entry point to the source vault resolution: the two paths
    relative to the clone root come from the caller, the password from the
    run, and the production vault wins over the default vault. Other tasks
    that must read secrets from the fresh clone
    (nextdns_setup_system_wide) import this function
    instead of reimplementing the source selection
    (project rules, General engineering requirements).
    """

    return _open_source_vault(
        *_resolve_source_vault(repo_root, production, default), password
    )


def _read_local_vault_password(kp: PyKeePass) -> str | None:
    """Runtime vault password from the source vault entry, or None.

    The entry is looked up by title in the root group, matching the flat
    structure of the source vault; a missing entry or an empty password value
    both mean the source vault cannot provide the runtime password, and None is
    returned.
    """

    entry = kp.find_entries(
        title=values.VAULT_PASSWORD_ENTRY_TITLE,
        group=kp.root_group,
        recursive=False,
        first=True,
    )
    if entry is None:
        return None
    password: str | None = entry.password
    if not password:
        return None
    return password


def _read_password_file(pass_file_path: Path) -> str | None:
    """Runtime vault password from the password file, or None.

    The file holds exactly the password with surrounding whitespace
    trimmed and no trailing newline, so the read applies the same
    trimming. An unreadable or empty file means no password is
    available.
    """

    try:
        return pass_file_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _copy_missing_root_entries(
    source_kp: PyKeePass, runtime_kp: PyKeePass
) -> bool:
    """Copy the source vault root entries missing from the runtime vault.

    A runtime vault created by an older run may lack entries the
    structure gained later, like the telemetry password; the source vault
    is the structure, so every root entry it carries and the runtime
    vault does not is copied. Machine-specific entries that tasks add to
    the runtime vault are not in the source vault and stay untouched.
    Returns True when at least one entry was copied.
    """

    existing_titles = {entry.title for entry in runtime_kp.root_group.entries}
    changed = False
    for entry in source_kp.root_group.entries:
        if entry.title in existing_titles:
            continue
        runtime_kp.add_entry(
            runtime_kp.root_group,
            entry.title,
            entry.username or "",
            entry.password or "",
            url=entry.url or "",
            notes=entry.notes or "",
        )
        _log(f"adding entry {entry.title!r} to the runtime vault")
        changed = True
    return changed


def _copy_missing_groups(
    source_kp: PyKeePass, runtime_kp: PyKeePass
) -> bool:
    """Copy the source subgroups and their entries missing from the runtime.

    A runtime vault created by an older run may lack a data subgroup the
    structure gained later, like port_forwarding_servers; the source vault
    is the structure, so every subgroup it carries and the runtime vault
    does not is created with its notes, and every entry of that subgroup
    missing from the runtime copy is copied. Existing groups and entries
    are never touched, so data the operator maintains in the source vault
    reaches the runtime vault without duplication or loss. Returns True
    when at least one group was created or one entry was copied.
    """

    existing_groups = {
        group.name: group for group in runtime_kp.root_group.subgroups
    }
    changed = False
    for source_group in source_kp.root_group.subgroups:
        runtime_group = existing_groups.get(source_group.name)
        if runtime_group is None:
            runtime_group = runtime_kp.add_group(
                runtime_kp.root_group,
                source_group.name,
                notes=source_group.notes or "",
            )
            _log(f"adding group {source_group.name!r} to the runtime vault")
            changed = True
        existing_titles = {entry.title for entry in runtime_group.entries}
        for entry in source_group.entries:
            if entry.title in existing_titles:
                continue
            runtime_kp.add_entry(
                runtime_group,
                entry.title,
                entry.username or "",
                entry.password or "",
                url=entry.url or "",
                notes=entry.notes or "",
            )
            _log(
                f"adding entry {entry.title!r} to group "
                f"{source_group.name!r} in the runtime vault"
            )
            changed = True
    return changed


def _sync_existing_runtime_vault(
    production_path: Path,
    default_path: Path,
    source_password: str | None,
) -> bool | None:
    """Sync the source structure entries and groups into the runtime vault.

    True means an entry or a group was copied and the vault saved, False
    means the runtime vault already carried every source entry, and None
    means the sync could not run: no source vault opens, the password file
    is missing or the runtime vault does not open with the local password.
    A failed sync leaves the runtime vault exactly as it was.
    """

    opened = _open_source_vault(production_path, default_path, source_password)
    if opened is None:
        _log("leaving the runtime vault as is: no source vault opened")
        return None
    source_kp, _ = opened
    local_password = _read_password_file(values.PASS_FILE_PATH)
    if local_password is None:
        _log(
            "leaving the runtime vault as is: password file missing or empty"
        )
        return None
    try:
        runtime_kp = PyKeePass(
            str(values.LOCAL_VAULT_PATH), password=local_password
        )
    except CredentialsError:
        _log("leaving the runtime vault as is: local password does not match")
        return None
    except Exception as exc:  # noqa: BLE001 - a broken vault stays as it is
        _log(f"leaving the runtime vault as is: cannot open: {exc}")
        return None
    changed = _copy_missing_root_entries(source_kp, runtime_kp)
    changed = _copy_missing_groups(source_kp, runtime_kp) or changed
    if not changed:
        return False
    runtime_kp.save(filename=str(values.LOCAL_VAULT_PATH))
    return True


def _write_local_vault(
    kp: PyKeePass,
    password: str,
    local_vault_path: Path,
    secrets_dir_mode: int,
    local_vault_file_mode: int,
) -> None:
    """Re-encrypt the opened source vault with the local password.

    The copy is written to the configured runtime path, so the source
    password never opens the runtime vault (the local password does). The
    directory is created and forced to the configured secrets directory
    mode, the file carries the configured vault file mode.
    """

    kp.password = password
    local_vault_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(local_vault_path.parent, secrets_dir_mode)
    kp.save(filename=str(local_vault_path))
    os.chmod(local_vault_path, local_vault_file_mode)


def _write_password_file(
    password: str,
    pass_file_path: Path,
    pass_dir_mode: int,
    pass_file_mode: int,
    pass_file_writable_mode: int,
) -> None:
    """Write the password file: trimmed password, no trailing newline.

    The file holds exactly the password: surrounding whitespace is trimmed
    and no newline is appended, so consumers that read the file get the
    password without post-processing. An existing password file carries
    the configured restrictive mode, so before a force rewrite it is made
    writable for its owner with the configured pass_file_writable_mode and
    the configured pass_file_mode is restored after the write.
    """

    pass_file_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(pass_file_path.parent, pass_dir_mode)
    if pass_file_path.exists():
        os.chmod(pass_file_path, pass_file_writable_mode)
    pass_file_path.write_text(password.strip(), encoding="utf-8")
    os.chmod(pass_file_path, pass_file_mode)


def _verify_local_vault(local_vault_path: Path, password: str) -> bool:
    """True when the written runtime vault opens with the local password.

    Opening the file is the proof that the re-encryption worked; a
    CredentialsError means the password file and the vault disagree.
    """

    try:
        PyKeePass(str(local_vault_path), password=password)
    except CredentialsError:
        return False
    return True


def task(ctx: Context) -> TaskResult:
    """Create the runtime secret vault; skip when the goal is reached.

    Without force the task skips when the runtime vault already exists.
    Otherwise it opens the first available source vault, reads the local
    vault password from it, writes the re-encrypted runtime vault and the
    password file with the configured modes, sets the configured owner of
    a file the run creates as root and verifies the runtime vault by
    opening it. A vault
    that cannot be opened, a missing or empty password entry and a failed
    write are journaled at the configured error priority and reported as
    warnings of a completed task: without a source vault or without the
    local password entry there is nothing to build from, so the run ends
    there, while a runtime vault that could not be written skips the
    password file and the verification that depend on it.
    """

    absent = missing_value_names(
        values, values.READ_VALUE_NAMES
    ) + missing_value_names(common_values, common_values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read, so no value is
        # touched before the names are known.
        return TaskResult(
            success=True,
            message=(
                "the local_vault_setup values are not declared, nothing was "
                "changed"
            ),
            warnings=(
                "the local_vault_setup values are not declared: "
                + ", ".join(absent),
            ),
        )
    owner_uid = ctx.config.engine.root_owner_uid
    owner_gid = ctx.config.engine.root_owner_gid
    force = ctx.task_name in ctx.force_tasks
    production_path, default_path = _resolve_source_vault(
        ctx.repo_root,
        common_values.SOURCE_VAULT_PRODUCTION,
        common_values.SOURCE_VAULT_DEFAULT,
    )

    if not force and values.LOCAL_VAULT_PATH.exists():
        _log(f"checking runtime vault {values.LOCAL_VAULT_PATH}: exists")
        synced = _sync_existing_runtime_vault(
            production_path, default_path, ctx.vault_password
        )
        if synced is None:
            return TaskResult(
                success=True,
                changed=False,
                message="runtime vault already exists",
            )
        return TaskResult(
            success=True,
            changed=synced,
            message=(
                "runtime vault synced with the source vault"
                if synced
                else "runtime vault already exists"
            ),
        )

    _log(f"checking runtime vault {values.LOCAL_VAULT_PATH}: absent")
    opened = _open_source_vault(production_path, default_path, ctx.vault_password)
    if opened is None:
        warning = (
            "cannot open any source vault: neither production nor default "
            "opened with the run password"
        )
        _log(warning, priority=values.ERROR_PRIORITY)
        return TaskResult(
            success=True,
            changed=False,
            message=warning,
            warnings=(warning,),
        )
    kp, source_path = opened

    _log(
        f"reading entry {values.VAULT_PASSWORD_ENTRY_TITLE!r} from {source_path}"
    )
    local_password = _read_local_vault_password(kp)
    if local_password is None:
        warning = (
            f"entry {values.VAULT_PASSWORD_ENTRY_TITLE!r} is missing or empty "
            "in the source vault"
        )
        _log(warning, priority=values.ERROR_PRIORITY)
        return TaskResult(
            success=True,
            changed=False,
            message=warning,
            warnings=(warning,),
        )
    _log("local vault password entry found")

    warnings: list[str] = []
    vault_written = True
    try:
        _log(
            f"writing runtime vault {values.LOCAL_VAULT_PATH} with local "
            "password"
        )
        _write_local_vault(
            kp,
            local_password.strip(),
            values.LOCAL_VAULT_PATH,
            values.SECRETS_DIR_MODE,
            values.LOCAL_VAULT_FILE_MODE,
        )
    except (OSError, ValueError) as exc:
        warning = f"cannot write runtime vault: {exc}"
        _log(warning, priority=values.ERROR_PRIORITY)
        warnings.append(warning)
        vault_written = False
    else:
        _log("runtime vault written")
        try:
            apply_owner(values.LOCAL_VAULT_PATH, owner_uid, owner_gid)
        except OSError:
            _log("cannot set owner of the runtime vault")

    if not vault_written:
        # The password file and the verification belong to the runtime
        # vault, so they are skipped while the reason stays visible.
        _log("skipping the password file and the verification")
        return TaskResult(
            success=True,
            changed=False,
            message="; ".join(warnings),
            warnings=tuple(warnings),
        )

    try:
        _log(f"writing password file {values.PASS_FILE_PATH}")
        _write_password_file(
            local_password,
            values.PASS_FILE_PATH,
            values.PASS_DIR_MODE,
            values.PASS_FILE_MODE,
            values.PASS_FILE_WRITABLE_MODE,
        )
    except (OSError, ValueError) as exc:
        warning = f"cannot write password file: {exc}"
        _log(warning, priority=values.ERROR_PRIORITY)
        warnings.append(warning)
    else:
        _log("password file written")
        try:
            apply_owner(values.PASS_FILE_PATH, owner_uid, owner_gid)
        except OSError:
            _log("cannot set owner of the password file")

    _log(f"verifying runtime vault {values.LOCAL_VAULT_PATH}")
    if not _verify_local_vault(values.LOCAL_VAULT_PATH, local_password.strip()):
        warning = "runtime vault verification failed"
        _log(
            "verification failed: runtime vault does not open with the local password",
            priority=values.ERROR_PRIORITY,
        )
        warnings.append(warning)
    else:
        _log("verification passed")
    message = f"runtime vault created from {source_path}"
    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=True, message=message, warnings=tuple(warnings)
    )
