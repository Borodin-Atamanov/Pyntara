"""Task nextdns_setup_system_wide: select and record the machine's NextDNS profile.

The task picks one NextDNS profile per machine, deterministically from
the hostname, and records its ID in a file for dnsproxy_setup and the
System Metrics collector (docs/spec/nextdns-profile.md). The profile
comes from the vault group named by VAULT_GROUP_TITLE, the ID is
sha256(hostname) modulo the pool size, so the same hostname always resolves
through the same account. The vaults are the source vaults of the fresh clone,
opened with the run password the way local_vault_setup opens them; the
runtime vault is only a fallback, because the copy may be stale and
predate the profile group. The task is idempotent: when the profile ID
file already carries the selected profile it reports done with no
changes; force mode rewrites the file, but the profile choice from the
hostname never changes.
"""

from __future__ import annotations

import os

from pykeepass import PyKeePass

from pyntara import metrics
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.nextdns_profile import select_profile_from_vault
from pyntara.tasks.local_vault_setup import open_source_vault
from pyntara.utils import apply_owner
from pyntara.values import common as common_values
from pyntara.values import missing_value_names
from pyntara.values import nextdns_setup_system_wide as values

# The module reads no repository path of its own: the source vault paths of
# local_vault_setup are resolved against the clone root the context carries,
# which the tests point at a fixture.


def _write_profile_id_file(
    profile_id: str,
    owner_uid: int,
    owner_gid: int,
) -> bool:
    """Record the selected profile ID for the System Metrics collector.

    The mode and the root ownership are applied through the shared
    apply_owner helper, so the owner is the pair the caller passes and no
    literal lives here. A failed write is journaled and reported, so the
    task fails loudly instead of silently losing the telemetry source.
    """

    path = common_values.PROFILE_ID_FILE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{profile_id}\n", encoding="utf-8")
        os.chmod(path, common_values.PROFILE_ID_FILE_MODE)
        apply_owner(path, owner_uid, owner_gid)
        return True
    except OSError as exc:
        _log(
            f"cannot write the profile ID file {path}: {exc}",
            priority=values.ERROR_PRIORITY,
        )
        return False


def _open_profile_vault(ctx: Context) -> PyKeePass | None:
    """The vault that carries the NextDNS profiles, or None.

    The source vaults of the fresh clone are the primary source: the
    production vault is tried first, then the default vault, both with
    the run password, through the shared open_source_vault of the
    local_vault_setup task (docs/spec/secrets-model.md). The runtime
    vault is only the fallback for a run without a vault password,
    because it is a copy made once by local_vault_setup and may be stale.
    """

    source = open_source_vault(
        ctx.repo_root,
        common_values.SOURCE_VAULT_PRODUCTION,
        common_values.SOURCE_VAULT_DEFAULT,
        ctx.vault_password,
    )
    if source is not None:
        return source[0]
    _log("source vaults unavailable, trying the runtime vault")
    return metrics.open_runtime_vault(ctx.config)


def task(ctx: Context) -> TaskResult:
    """Select a NextDNS profile and record its ID; skip when done.

    The vault is opened from the source vaults of the fresh clone with the
    run password, the way local_vault_setup opens them; the runtime vault
    is only the fallback. The profile group is read from the vault and the
    profile is derived from the hostname. A vault that cannot be opened, a
    missing profile group or an empty profile pool is a warning of a
    completed task: writing the profile ID is the only step of the task, so
    there is nothing else to do and the file is left untouched. The task is
    idempotent: when the profile ID file already carries the selected
    profile it reports done with no changes; force mode rewrites the file.
    """

    absent = missing_value_names(
        values, values.READ_VALUE_NAMES
    ) + missing_value_names(common_values, common_values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks.
        return TaskResult(
            success=True,
            message=(
                "the nextdns_setup_system_wide values are not declared, "
                "nothing was changed"
            ),
            warnings=(
                "the nextdns_setup_system_wide values are not declared: "
                + ", ".join(absent),
            ),
        )
    owner_uid = ctx.config.engine.root_owner_uid
    owner_gid = ctx.config.engine.root_owner_gid
    kp = _open_profile_vault(ctx)
    if kp is None:
        warning = "cannot open a vault with the NextDNS profiles"
        return TaskResult(
            success=True,
            changed=False,
            message=warning,
            warnings=(warning,),
        )
    profile_id = select_profile_from_vault(kp, values.VAULT_GROUP_TITLE)
    if profile_id is None:
        warning = (
            f"cannot derive a NextDNS profile from vault group "
            f"{values.VAULT_GROUP_TITLE!r} and hostname"
        )
        return TaskResult(
            success=True,
            changed=False,
            message=warning,
            warnings=(warning,),
        )

    try:
        existing = common_values.PROFILE_ID_FILE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing == profile_id and ctx.task_name not in ctx.force_tasks:
        return TaskResult(
            success=True,
            changed=False,
            message="profile ID file already carries the selected profile",
        )

    if not _write_profile_id_file(profile_id, owner_uid, owner_gid):
        warning = "cannot record the NextDNS profile ID"
        return TaskResult(
            success=True,
            changed=False,
            message=warning,
            warnings=(warning,),
        )
    return TaskResult(
        success=True,
        changed=True,
        message=(
            f"Selected NextDNS profile {profile_id}; resolver configuration "
            "is owned by dnsproxy_setup"
        ),
    )
