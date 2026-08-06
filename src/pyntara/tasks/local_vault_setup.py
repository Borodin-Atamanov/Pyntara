"""Task local_vault_setup: create the runtime secret vault on the machine.

The runtime secret database at the configured local_vault_path and its
password file at pass_file_path are created so services that start after
install can decrypt the vault without user input
(docs/spec/secrets-model.md). The copy is re-encrypted with the password
from the vault_password_entry_title entry of the source vault, so the
source password never opens the runtime vault. The entry lives in the
root group of the vault, because the structure is flat (the
[vault_structure] table in config.toml). All paths and the entry title
come from config.toml through ctx.config.local_vault_setup (architecture
contract section 3). The source vault is not fixed: the production vault
is tried first, then the default vault, both with the password from
Context; when neither opens, the task journals a serious error at syslog
level 3 and fails without stopping the run. The task is idempotent:
without force it skips when the runtime vault already exists; force mode
rewrites the vault and the password file. Passwords are written to files
trimmed of surrounding whitespace and strictly without a trailing newline.
"""

from __future__ import annotations

import os
from pathlib import Path

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

from pyntara.config import LocalVaultSetupConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_source_vault(cfg: LocalVaultSetupConfig) -> tuple[Path, Path]:
    """Source vault paths resolved against the repository root.

    The configured source paths are relative to the repository root, so
    the clone can live anywhere on the machine (/var/cache/pyntara/repo
    in production, a temporary directory in tests).
    """

    return (
        REPO_ROOT / cfg.source_vault_production,
        REPO_ROOT / cfg.source_vault_default,
    )


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


def _read_local_vault_password(kp: PyKeePass, cfg: LocalVaultSetupConfig) -> str | None:
    """Runtime vault password from the source vault entry, or None.

    The entry is looked up by title in the root group, matching the flat
    structure of the [vault_structure] table; a missing entry or an empty
    password value both mean the source vault cannot provide the runtime
    password, and None is returned.
    """

    entry = kp.find_entries(
        title=cfg.vault_password_entry_title,
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
    directory is created and forced to 0700, the file is chmodded to 0640.
    """

    kp.password = password
    local_vault_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(local_vault_path.parent, secrets_dir_mode)
    kp.save(filename=str(local_vault_path))
    os.chmod(local_vault_path, local_vault_file_mode)


def _write_password_file(
    password: str, pass_file_path: Path, pass_dir_mode: int, pass_file_mode: int
) -> None:
    """Write the password file: trimmed password, no trailing newline.

    The file holds exactly the password: surrounding whitespace is trimmed
    and no newline is appended, so consumers that read the file get the
    password without post-processing. An existing password file is read-only
    (0400), so before a force rewrite it is made writable for its owner and
    the restrictive mode is restored after the write.
    """

    pass_file_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(pass_file_path.parent, pass_dir_mode)
    if pass_file_path.exists():
        os.chmod(pass_file_path, 0o600)
    pass_file_path.write_text(password.strip(), encoding="utf-8")
    os.chmod(pass_file_path, pass_file_mode)


def _ensure_owner(path: Path) -> None:
    """Set owner root:root when the process runs as root.

    The installer runs under sudo, so the ownership is applied on real
    machines; non-root test runs skip the chown, because it would fail
    without privileges.
    """

    if os.geteuid() == 0:
        os.chown(path, 0, 0)


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
    password file with the fixed modes, sets the owner to root:root when
    running as root and verifies the runtime vault by opening it. A vault
    that cannot be opened, a missing or empty password entry and a failed
    write are errors: the serious ones are journaled at syslog level 3,
    the task returns success=False and the runner continues.
    """

    cfg = ctx.config.local_vault_setup
    force = "local_vault_setup" in ctx.force_tasks
    production_path, default_path = _resolve_source_vault(cfg)

    if not force and cfg.local_vault_path.exists():
        pass_state = "present" if cfg.pass_file_path.exists() else "missing"
        _log(
            f"checking runtime vault {cfg.local_vault_path}: exists, "
            f"password file {pass_state}"
        )
        _log("target state already reached, skipping")
        return TaskResult(
            success=True,
            changed=False,
            message=f"runtime vault already exists, password file {pass_state}",
        )

    _log(f"checking runtime vault {cfg.local_vault_path}: absent")
    opened = _open_source_vault(production_path, default_path, ctx.vault_password)
    if opened is None:
        _log(
            "cannot open any source vault: production and default did not open",
            priority=cfg.error_priority,
        )
        return TaskResult(
            success=False,
            error=(
                "cannot open any source vault: neither production nor default "
                "opened with the run password"
            ),
        )
    kp, source_path = opened

    _log(f"reading entry {cfg.vault_password_entry_title!r} from {source_path}")
    local_password = _read_local_vault_password(kp, cfg)
    if local_password is None:
        _log(
            "cannot read local vault password: entry missing or empty",
            priority=cfg.error_priority,
        )
        return TaskResult(
            success=False,
            error=(
                f"entry {cfg.vault_password_entry_title!r} is missing or empty "
                "in the source vault"
            ),
        )
    _log("local vault password entry found")

    try:
        _log(f"writing runtime vault {cfg.local_vault_path} with local password")
        _write_local_vault(
            kp,
            local_password.strip(),
            cfg.local_vault_path,
            cfg.secrets_dir_mode,
            cfg.local_vault_file_mode,
        )
    except (OSError, ValueError) as exc:
        _log(
            f"cannot write runtime vault {cfg.local_vault_path}: {exc}",
            priority=cfg.error_priority,
        )
        return TaskResult(success=False, error=f"cannot write runtime vault: {exc}")
    _log("runtime vault written")
    try:
        _ensure_owner(cfg.local_vault_path)
    except OSError:
        _log("cannot set owner of the runtime vault")

    try:
        _log(f"writing password file {cfg.pass_file_path}")
        _write_password_file(
            local_password, cfg.pass_file_path, cfg.pass_dir_mode, cfg.pass_file_mode
        )
    except (OSError, ValueError) as exc:
        _log(
            f"cannot write password file {cfg.pass_file_path}: {exc}",
            priority=cfg.error_priority,
        )
        return TaskResult(success=False, error=f"cannot write password file: {exc}")
    _log("password file written")
    try:
        _ensure_owner(cfg.pass_file_path)
    except OSError:
        _log("cannot set owner of the password file")

    _log(f"verifying runtime vault {cfg.local_vault_path}")
    if not _verify_local_vault(cfg.local_vault_path, local_password.strip()):
        _log(
            "verification failed: runtime vault does not open with the local password",
            priority=cfg.error_priority,
        )
        return TaskResult(
            success=False,
            changed=True,
            error="runtime vault verification failed",
        )
    _log("verification passed")
    return TaskResult(
        success=True,
        changed=True,
        message=f"runtime vault created from {source_path}",
    )
