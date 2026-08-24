"""Task nextdns_setup_system_wide: select and record the machine's NextDNS profile.

The task picks one NextDNS profile per machine, deterministically from
the hostname, and records its ID in a file for dnsproxy_setup and the
System Metrics collector (docs/spec/nextdns-profile.md). The profile
comes from the vault group named by
nextdns_setup_system_wide.vault_group_title, the ID is sha256(hostname)
modulo the pool size, so the same hostname always resolves through the
same account. The vaults are the source vaults of the fresh clone,
opened with the run password the way local_vault_setup opens them; the
runtime vault is only a fallback, because the copy may be stale and
predate the profile group. The task is idempotent: when the profile ID
file already carries the selected profile it reports done with no
changes; force mode rewrites the file, but the profile choice from the
hostname never changes.
"""

from __future__ import annotations

import os
from pathlib import Path

from pykeepass import PyKeePass

from pyntara import metrics
from pyntara.config import NextdnsSetupSystemWideConfig
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.nextdns_profile import select_profile_from_vault
from pyntara.tasks.local_vault_setup import open_source_vault

# Module-level path constant is monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide):
# the source vault paths of local_vault_setup are resolved against the
# repository root, so the clone can live anywhere on the machine. It is
# an approved repository layout path exception (architecture contract,
# Configuration).
REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_profile_id_file(cfg: NextdnsSetupSystemWideConfig, profile_id: str) -> bool:
    """Record the selected profile ID for the System Metrics collector.

    The mode and the root ownership are applied; a failed write is
    journaled and reported, so the task fails loudly instead of silently
    losing the telemetry source.
    """

    path = cfg.profile_id_file_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{profile_id}\n", encoding="utf-8")
        os.chmod(path, cfg.profile_id_file_mode)
        if os.geteuid() == 0:
            os.chown(path, 0, 0)
        return True
    except OSError as exc:
        _log(
            f"cannot write the profile ID file {path}: {exc}",
            priority=cfg.error_priority,
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

    source = open_source_vault(ctx.config.local_vault_setup, ctx.vault_password)
    if source is not None:
        return source[0]
    _log("source vaults unavailable, trying the runtime vault")
    return metrics.open_runtime_vault(ctx.config)


def task(ctx: Context) -> TaskResult:
    """Select a NextDNS profile and record its ID; skip when done.

    The vault is opened from the source vaults of the fresh clone with the
    run password, the way local_vault_setup opens them; the runtime vault
    is only the fallback. The profile group is read from the vault and the
    profile is derived from the hostname. A missing profile group or an
    empty profile pool is a failure reported in the result: the profile
    ID file is never touched then. The task is idempotent: when the
    profile ID file already carries the selected profile it reports done
    with no changes; force mode rewrites the file.
    """

    cfg = ctx.config.nextdns_setup_system_wide
    kp = _open_profile_vault(ctx)
    if kp is None:
        return TaskResult(
            success=False,
            changed=False,
            error="cannot open a vault with the NextDNS profiles",
        )
    profile_id = select_profile_from_vault(kp, cfg.vault_group_title)
    if profile_id is None:
        return TaskResult(
            success=False,
            changed=False,
            error=(
                f"cannot derive a NextDNS profile from vault group "
                f"{cfg.vault_group_title!r} and hostname"
            ),
        )

    try:
        existing = cfg.profile_id_file_path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing == profile_id and "nextdns_setup_system_wide" not in ctx.force_tasks:
        return TaskResult(
            success=True,
            changed=False,
            message="profile ID file already carries the selected profile",
        )

    if not _write_profile_id_file(cfg, profile_id):
        return TaskResult(success=False, error="cannot record the NextDNS profile ID")
    return TaskResult(
        success=True,
        changed=True,
        message=(
            f"Selected NextDNS profile {profile_id}; resolver configuration "
            "is owned by dnsproxy_setup"
        ),
    )
