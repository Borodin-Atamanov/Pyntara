"""Cloning, updating and deploying a browser defaults repository.

chrome_setup and firefox_setup each keep a repository of browser defaults in a
root cache, bring it to the declared branch on every run and deploy its system/
tree under the system root, so the version control commands, the branch, the
tree name and the system root live in values/common.py and the work lives here
once. A section passes its own repository url and clone directory.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pyntara.utils import apply_owner, run_command, substituted_command
from pyntara.values import common as common_values


def sync_repository(
    *, url: str, directory: Path, timeout: float
) -> tuple[bool, str | None]:
    """Clone the repository when it is missing, else bring it to the branch.

    The clone, the fetch and the two revision queries come from the shared
    values as command templates, so the flags of the version control tool are
    values and not code. A repository already at the fetched revision is left
    alone; otherwise it is reset to the fetched revision, so the deployed files
    always come from the declared branch.
    """

    placeholders = {
        "url": url,
        "ref": common_values.SETTINGS_REPO_REF,
        "dir": str(directory),
    }
    try:
        if not (directory / ".git").is_dir():
            directory.parent.mkdir(parents=True, exist_ok=True)
            run_command(
                substituted_command(common_values.SETTINGS_CLONE_COMMAND, placeholders),
                timeout=timeout,
            )
            return True, None
        run_command(
            substituted_command(common_values.SETTINGS_FETCH_COMMAND, placeholders),
            timeout=timeout,
        )
        head = run_command(
            substituted_command(
                common_values.SETTINGS_REVISION_COMMAND,
                {**placeholders, "revision": "HEAD"},
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
        fetched = run_command(
            substituted_command(
                common_values.SETTINGS_REVISION_COMMAND,
                {**placeholders, "revision": "FETCH_HEAD"},
            ),
            check=False,
            capture=True,
            timeout=timeout,
        )
        if (
            head.returncode == 0
            and fetched.returncode == 0
            and head.stdout.strip() == fetched.stdout.strip()
        ):
            return False, None
        run_command(
            substituted_command(
                common_values.SETTINGS_RESET_COMMAND,
                {**placeholders, "revision": "FETCH_HEAD"},
            ),
            timeout=timeout,
        )
        return True, None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"cannot update the browser settings repository: {exc}"


def deploy_tree(
    source_root: Path,
    target_root: Path,
    *,
    force: bool,
    skip_relative_paths: tuple[str, ...] = (),
    owner_ids: tuple[int, int] | None = None,
) -> tuple[bool, list[str]]:
    """Copy one tree under another path; (changed, warnings).

    Every file of source_root lands under target_root with its relative path
    preserved, carrying the mode of every deployed file, and is written only
    when its bytes differ (or in force mode). A relative path that equals a
    skipped path or stands below it is left out, so one caller applies the
    system tree of the repository and another caller the rest of it. The owner
    pair is applied to every written file when it is given; a caller whose
    target belongs to the desktop user hands the whole directory over
    afterwards instead, because that user is not named by a pair of ids here. A
    per-file failure is a warning, never a fatal error.
    """

    skipped = [Path(name) for name in skip_relative_paths]
    changed = False
    warnings: list[str] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        if any(relative == name or name in relative.parents for name in skipped):
            continue
        target = target_root / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if (
                not force
                and target.is_file()
                and target.read_bytes() == path.read_bytes()
            ):
                continue
            shutil.copyfile(path, target)
            target.chmod(common_values.LAUNCHER_FILE_MODE)
            if owner_ids is not None:
                apply_owner(target, owner_ids[0], owner_ids[1])
            changed = True
        except OSError as exc:
            warnings.append(f"cannot deploy {relative}: {exc}")
    return changed, warnings


def deploy_system_tree(
    directory: Path, *, force: bool, owner_uid: int, owner_gid: int
) -> tuple[bool, list[str]]:
    """Deploy the repository system/ tree under the system root; (changed, notes).

    The files are root-owned, copied only when the target differs (or in force
    mode); a repository without that tree is a note.
    """

    source_root = directory / common_values.SETTINGS_SYSTEM_TREE_RELATIVE_PATH
    if not source_root.is_dir():
        return False, ["the settings repository carries no system/ tree"]
    return deploy_tree(
        source_root,
        common_values.SYSTEM_ROOT,
        force=force,
        owner_ids=(owner_uid, owner_gid),
    )
