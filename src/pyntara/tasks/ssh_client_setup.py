"""Task ssh_client_setup: configure the system-wide SSH client.

The task patches the client configuration through a drop-in file at the
configured ssh_config_dropin_path, never through ssh_config itself:
ssh_config is only checked for an Include directive that pulls the
drop-in directory in, because a missing Include means the drop-in
would be silently ignored. Directives are written through augeas under
the container block the config names, which applies them to every
connection; augeas parses
the real syntax and updates only what differs: a directive that is
already present with the same value is left untouched, a directive with
a different value is updated, a directive that is no longer configured
is removed, and the ownership comment is guaranteed. An empty
directives list removes the drop-in, so the task can revoke its own
settings. After a change the effective configuration is verified with
ssh -G, which prints the result of the whole Include chain, so a
directive overridden by another file or a keyword the client does not
know is reported as an error instead of being silently accepted. The
task is idempotent: it skips when ssh_config pulls the drop-in
directory in and the drop-in matches the configured directives through
augeas; force mode rewrites the drop-in and verifies it again.
"""

from __future__ import annotations

import subprocess

from pyntara.augeas import ensure_augtool, include_covers_dropin, sync_dropin
from pyntara.config import SshClientSetupConfig, SshDirective
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command


def _verify_effective_config(
    cfg: SshClientSetupConfig,
    directives: tuple[SshDirective, ...],
    timeout: float,
) -> str | None:
    """Error text when a configured directive is not effective; None when OK.

    The configured probe command prints the effective client configuration
    after every file of the Include chain is applied, so the check is
    independent of the version and of other files in the drop-in
    directory: a directive that a later file overrides, or a keyword the
    client does not know, is reported as an error instead of being
    silently accepted.
    """

    try:
        result = run_command(
            list(cfg.effective_config_command),
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cannot probe the effective client configuration: {exc}"
    if result.returncode != 0:
        return f"ssh -G exited {result.returncode}: {result.stderr.strip()}"
    effective: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(" ")
        if sep:
            effective[key.casefold()] = value.strip()
    for directive in directives:
        key = directive.name.casefold()
        actual = effective.get(key)
        if actual is None or actual.casefold() != directive.value.casefold():
            return (
                f"ssh -G reports {key} as {actual or 'unset'}, "
                f"expected {directive.value}"
            )
    return None


def task(ctx: Context) -> TaskResult:
    """Patch the system-wide SSH client configuration; report the result.

    The goal is reached when ssh_config pulls the drop-in directory in
    and the drop-in matches the configured directives through augeas;
    the task then returns changed=False. Otherwise it syncs the drop-in
    and verifies the effective configuration with ssh -G.
    """

    cfg = ctx.config.ssh_client_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = ctx.task_name in ctx.force_tasks
    owner_uid = ctx.config.engine.root_owner_uid
    owner_gid = ctx.config.engine.root_owner_gid

    include_ok = include_covers_dropin(
        cfg.ssh_config_path, cfg.ssh_config_dropin_path
    )
    _log(
        f"checking Include directive in {cfg.ssh_config_path}: "
        f"{'found' if include_ok else 'missing'}"
    )
    if not include_ok:
        return TaskResult(
            success=False,
            error=(
                f"{cfg.ssh_config_path} has no Include directive covering "
                f"{cfg.ssh_config_dropin_path.parent}"
            ),
        )

    augtool_error = ensure_augtool(
        cfg.augeas_tools_package_name,
        status_timeout=cfg.package_status_timeout_seconds,
        install_timeout=timeout,
        retries=cfg.install_retries,
        skip_update=ctx.skip_apt_update,
    )
    if augtool_error is not None:
        return TaskResult(success=False, error=augtool_error)

    directives = tuple(
        (directive.name, directive.value) for directive in cfg.directives
    )
    try:
        changed, _ = sync_dropin(
            ctx.config.engine,
            cfg.ssh_config_dropin_path,
            directives,
            cfg.dropin_file_mode,
            force,
            cfg.augeas_lens,
            cfg.dropin_header,
            timeout,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            container=(cfg.augeas_container, cfg.augeas_container_value),
        )
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    if changed:
        _log("drop-in synced through augeas")

    if (changed or force) and cfg.directives:
        verify = _verify_effective_config(cfg, cfg.directives, timeout)
        if verify is not None:
            return TaskResult(success=False, changed=changed, error=verify)
        _log("effective configuration verified through ssh -G")

    if not changed and not force:
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    return TaskResult(
        success=True,
        changed=True,
        message="system-wide SSH client configuration synced",
    )
