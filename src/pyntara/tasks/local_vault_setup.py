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
without force it keeps an existing runtime vault the machine can open and
copies every source entry missing from it, in the root group and in the
subgroups, so a vault created by an older run gains the entries and
groups the structure gained later; a password file that is missing, empty
or out of step with the vault that opens is written again, and a vault
that no known password opens is renamed to the rescue path and built
again from the source vault, because a machine whose runtime vault cannot
be opened has lost the secrets its services need and nobody there can
repair it by hand; force mode rewrites the vault and the password file.
Every repair, and every reason a repair could not run, is reported as a
warning, so a machine left without a usable runtime vault never appears
as a plain success. Passwords are written to files trimmed of surrounding
whitespace and strictly without a trailing newline.
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
from pyntara.values import engine as engine_values
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


def _copy_missing_root_entries(source_kp: PyKeePass, runtime_kp: PyKeePass) -> bool:
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


def _copy_missing_groups(source_kp: PyKeePass, runtime_kp: PyKeePass) -> bool:
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

    existing_groups = {group.name: group for group in runtime_kp.root_group.subgroups}
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


def _open_runtime_vault(
    local_vault_path: Path, password: str
) -> PyKeePass | None:
    """Open the runtime vault with one password; None when it does not open.

    An empty file, a truncated file and a file written with another password
    all give the same answer, because none of them hands the machine its
    secrets: the library raises StreamError for the first two and
    CredentialsError for the third.
    """

    try:
        return PyKeePass(str(local_vault_path), password=password)
    except Exception as exc:  # noqa: BLE001 - every open failure needs one repair
        _log(f"runtime vault {local_vault_path} does not open: {exc}")
        return None


def _open_runtime_vault_with_known_passwords(
    local_password: str, pass_file_password: str | None
) -> tuple[PyKeePass, str] | None:
    """Open the runtime vault with the password the machine has or the source one.

    The value of the password file is tried first, because that file is what
    the services of the machine read, then the value of the source vault
    entry. The password that opened the vault comes back with it, so the
    caller can write the password file whenever the two disagree. None means
    the vault opens with neither, which is the vault that has lost its
    secrets.
    """

    for password in (pass_file_password, local_password):
        if not password:
            continue
        opened = _open_runtime_vault(values.LOCAL_VAULT_PATH, password)
        if opened is not None:
            return opened, password
    return None


def _merge_source_structure_into_runtime_vault(
    source_kp: PyKeePass, runtime_kp: PyKeePass
) -> bool:
    """Copy the source entries and groups the runtime vault lacks, then save.

    True means an entry or a group was copied and the vault saved, False
    means the runtime vault already carried every source entry and group.
    """

    changed = _copy_missing_root_entries(source_kp, runtime_kp)
    changed = _copy_missing_groups(source_kp, runtime_kp) or changed
    if not changed:
        return False
    runtime_kp.save(filename=str(values.LOCAL_VAULT_PATH))
    return True


def _keep_unreadable_runtime_vault() -> str:
    """Rename the runtime vault to the rescue path; the sentence to report.

    A file that no known password opens may still hold entries an operator can
    recover by hand, so it is kept instead of being overwritten. The rescue
    file is a rescue copy and not a backup chain, so the previous copy is
    replaced rather than added to, which keeps the rescue copies bounded.
    """

    rescue_path = values.LOCAL_VAULT_RESCUE_PATH
    try:
        rescue_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(rescue_path.parent, values.SECRETS_DIR_MODE)
        os.replace(values.LOCAL_VAULT_PATH, rescue_path)
    except OSError as exc:
        _log(f"cannot keep the unreadable runtime vault: {exc}")
        return f"the unreadable file could not be kept: {exc}"
    _log(f"unreadable runtime vault kept at {rescue_path}")
    return f"the unreadable file is kept at {rescue_path}"


def _repair_or_sync_existing_runtime_vault(
    production_path: Path,
    default_path: Path,
    source_password: str | None,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, tuple[str, ...], str]:
    """Bring an existing runtime vault to a state the machine can use.

    The answer is (changed, warnings, message). A vault the password file
    opens is kept and gains the source entries and groups it lacks; a
    password file that is missing, empty or out of step while the vault opens
    with the password of the source vault entry is written again, because the
    file is what the services read and the entries the tasks wrote into the
    vault live only there; a vault that no known password opens has lost its
    secrets, so it is renamed to the rescue path and built again from the
    source vault. A repair, and every reason a repair could not run, is
    reported as a warning, so a machine left without a usable runtime vault
    never appears as a plain success.
    """

    opened = _open_source_vault(production_path, default_path, source_password)
    if opened is None:
        warning = (
            "runtime vault not checked: no source vault opened with the run password"
        )
        _log("leaving the runtime vault as is: no source vault opened")
        return False, (warning,), "runtime vault already exists"
    source_kp, source_path = opened

    local_password = _read_local_vault_password(source_kp)
    if local_password is None:
        warning = (
            "runtime vault not checked: entry "
            f"{values.VAULT_PASSWORD_ENTRY_TITLE!r} is missing or empty "
            "in the source vault"
        )
        _log("leaving the runtime vault as is: no local password in the source vault")
        return False, (warning,), "runtime vault already exists"
    local_password = local_password.strip()

    pass_file_password = _read_password_file(values.PASS_FILE_PATH)
    opened_runtime = _open_runtime_vault_with_known_passwords(
        local_password, pass_file_password
    )
    if opened_runtime is None:
        kept = _keep_unreadable_runtime_vault()
        warning = (
            "runtime vault does not open with any known password, so it was built "
            f"again from {source_path}; {kept}"
        )
        _log(warning, priority=values.ERROR_PRIORITY)
        written, write_warnings = _write_runtime_vault_and_password_file(
            source_kp, local_password, owner_uid, owner_gid
        )
        return (
            written,
            (warning, *write_warnings),
            f"runtime vault rebuilt from {source_path}",
        )
    runtime_kp, password_of_the_vault = opened_runtime

    warnings: list[str] = []
    done: list[str] = []
    if password_of_the_vault != pass_file_password:
        # The vault opens with the password of the source entry while the
        # password file is missing, empty or out of step; the vault is kept
        # because the entries the tasks wrote into it live only there.
        password_warning = _write_password_file_with_owner(
            password_of_the_vault, owner_uid, owner_gid
        )
        if password_warning is None:
            done.append("the password file was written again")
        else:
            warnings.append(password_warning)
    if _merge_source_structure_into_runtime_vault(source_kp, runtime_kp):
        done.append("the missing source entries and groups were copied")
    message = (
        "runtime vault kept; " + " and ".join(done)
        if done
        else "runtime vault already exists"
    )
    return bool(done), tuple(warnings), message


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
    mode, the file carries the configured vault file mode. The copy goes to
    a temporary file next to the target and is moved onto it, so an
    interruption inside the write leaves the previous file or the complete
    new one, never a truncated vault where the machine reads it.
    """

    kp.password = password
    local_vault_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(local_vault_path.parent, secrets_dir_mode)
    temporary_path = local_vault_path.with_name(
        local_vault_path.name + values.LOCAL_VAULT_TEMPORARY_SUFFIX
    )
    kp.save(filename=str(temporary_path))
    os.chmod(temporary_path, local_vault_file_mode)
    os.replace(temporary_path, local_vault_path)


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

    Opening the file is the proof that the re-encryption worked. A
    CredentialsError means the password file and the vault disagree, and a
    StreamError means the file itself is unreadable, so every open failure
    is a failed verification and none of them is raised to the caller.
    """

    try:
        PyKeePass(str(local_vault_path), password=password)
    except Exception:  # noqa: BLE001 - every open failure is a failed verification
        return False
    return True


def _write_password_file_with_owner(
    password: str, owner_uid: int, owner_gid: int
) -> str | None:
    """Write the password file for a password and set its owner.

    None means the file was written, otherwise the warning that names why it
    was not, so the caller reports it without building the message again.
    """

    try:
        _log(f"writing password file {values.PASS_FILE_PATH}")
        _write_password_file(
            password,
            values.PASS_FILE_PATH,
            values.PASS_DIR_MODE,
            values.PASS_FILE_MODE,
            values.PASS_FILE_WRITABLE_MODE,
        )
    except (OSError, ValueError) as exc:
        warning = f"cannot write password file: {exc}"
        _log(warning, priority=values.ERROR_PRIORITY)
        return warning
    _log("password file written")
    try:
        apply_owner(values.PASS_FILE_PATH, owner_uid, owner_gid)
    except OSError:
        _log("cannot set owner of the password file")
    return None


def _write_runtime_vault_and_password_file(
    source_kp: PyKeePass,
    local_password: str,
    owner_uid: int,
    owner_gid: int,
) -> tuple[bool, list[str]]:
    """Write the runtime vault, its password file and the verification.

    True means the runtime vault was written; the list holds the warnings of
    the steps that could not run, so the caller reports what happened instead
    of a plain success. A vault that could not be written skips the password
    file and the verification, because both belong to it.
    """

    try:
        _log(f"writing runtime vault {values.LOCAL_VAULT_PATH} with local password")
        _write_local_vault(
            source_kp,
            local_password,
            values.LOCAL_VAULT_PATH,
            values.SECRETS_DIR_MODE,
            values.LOCAL_VAULT_FILE_MODE,
        )
    except (OSError, ValueError) as exc:
        warning = f"cannot write runtime vault: {exc}"
        _log(warning, priority=values.ERROR_PRIORITY)
        return False, [warning]
    _log("runtime vault written")
    try:
        apply_owner(values.LOCAL_VAULT_PATH, owner_uid, owner_gid)
    except OSError:
        _log("cannot set owner of the runtime vault")

    warnings: list[str] = []
    password_warning = _write_password_file_with_owner(
        local_password, owner_uid, owner_gid
    )
    if password_warning is not None:
        warnings.append(password_warning)

    _log(f"verifying runtime vault {values.LOCAL_VAULT_PATH}")
    if not _verify_local_vault(values.LOCAL_VAULT_PATH, local_password):
        warning = "runtime vault verification failed"
        _log(
            "verification failed: runtime vault does not open with the local password",
            priority=values.ERROR_PRIORITY,
        )
        warnings.append(warning)
    else:
        _log("verification passed")
    return True, warnings


def task(ctx: Context) -> TaskResult:
    """Create the runtime secret vault, and repair a vault the machine lost.

    Without force an existing runtime vault is kept: the source entries and
    groups it lacks are copied into it, a password file that is missing, empty
    or out of step is written again with the password that opens the vault, and
    a vault that no known password opens is renamed to the rescue path and
    built again from the source vault. Otherwise the task opens the first
    available source vault, reads the local vault password from it, writes the
    re-encrypted runtime vault and the password file with the configured modes,
    sets the configured owner of a file the run creates as root and verifies
    the runtime vault by opening it. A vault
    that cannot be opened, a missing or empty password entry and a failed
    write are journaled at the configured error priority and reported as
    warnings of a completed task: without a source vault or without the
    local password entry there is nothing to build from, so the run ends
    there, while a runtime vault that could not be written skips the
    password file and the verification that depend on it.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES) + missing_value_names(
        common_values, common_values.READ_VALUE_NAMES
    )
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read, so no value is
        # touched before the names are known.
        return TaskResult(
            success=True,
            message=(
                "the local_vault_setup values are not declared, nothing was changed"
            ),
            warnings=(
                "the local_vault_setup values are not declared: " + ", ".join(absent),
            ),
        )
    owner_uid = engine_values.ROOT_OWNER_UID
    owner_gid = engine_values.ROOT_OWNER_GID
    force = ctx.task_name in ctx.force_tasks
    production_path, default_path = _resolve_source_vault(
        ctx.repo_root,
        common_values.SOURCE_VAULT_PRODUCTION,
        common_values.SOURCE_VAULT_DEFAULT,
    )

    if not force and values.LOCAL_VAULT_PATH.exists():
        _log(f"checking runtime vault {values.LOCAL_VAULT_PATH}: exists")
        changed, warnings, message = _repair_or_sync_existing_runtime_vault(
            production_path,
            default_path,
            ctx.vault_password,
            owner_uid,
            owner_gid,
        )
        return TaskResult(
            success=True,
            changed=changed,
            message=message,
            warnings=warnings,
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

    _log(f"reading entry {values.VAULT_PASSWORD_ENTRY_TITLE!r} from {source_path}")
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

    written, vault_warnings = _write_runtime_vault_and_password_file(
        kp, local_password.strip(), owner_uid, owner_gid
    )
    if not written:
        # The password file and the verification belong to the runtime
        # vault, so they are skipped while the reason stays visible.
        _log("skipping the password file and the verification")
        return TaskResult(
            success=True,
            changed=False,
            message="; ".join(vault_warnings),
            warnings=tuple(vault_warnings),
        )

    message = f"runtime vault created from {source_path}"
    if vault_warnings:
        message = f"{message}; warnings: {'; '.join(vault_warnings)}"
    return TaskResult(
        success=True, changed=True, message=message, warnings=tuple(vault_warnings)
    )
