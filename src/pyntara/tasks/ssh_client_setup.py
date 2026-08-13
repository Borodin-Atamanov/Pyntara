"""Task ssh_client_setup: configure the system-wide SSH client.

The task patches the client configuration through a drop-in file at the
configured ssh_config_dropin_path, never through ssh_config itself:
ssh_config is only checked for an Include directive that pulls the
drop-in directory in, because a missing Include means the drop-in
would be silently ignored. Directives are written through augeas under
the Host block, which applies them to every connection; augeas parses
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

from pyntara.augeas import include_covers_dropin, sync_dropin
from pyntara.config import SshDirective
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import run_command

# The ownership comment of the drop-in, without the leading hash:
# augeas stores and writes comment values without it.
DROPIN_HEADER = "Managed by the Pyntara ssh_client_setup task."

# augeas lens for the ssh_config syntax; directives live under the Host
# block, so the container node is "Host".
SSH_CLIENT_LENS = "Ssh.lns"
SSH_CLIENT_CONTAINER = "Host"


def _verify_effective_config(
    directives: tuple[SshDirective, ...], timeout: float
) -> str | None:
    """Error text when a configured directive is not effective; None when OK.

    ssh -G prints the effective client configuration after every file of
    the Include chain is applied, so the check is independent of the
    version and of other files in the drop-in directory: a directive
    that a later file overrides, or a keyword the client does not know,
    is reported as an error instead of being silently accepted.
    """

    try:
        result = run_command(
            ["ssh", "-G", "example.com"],
            check=False,
            capture=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cannot run ssh -G: {exc}"
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
    force = "ssh_client_setup" in ctx.force_tasks

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

    directives = tuple(
        (directive.name, directive.value) for directive in cfg.directives
    )
    try:
        changed, _ = sync_dropin(
            cfg.ssh_config_dropin_path,
            directives,
            cfg.dropin_file_mode,
            force,
            SSH_CLIENT_LENS,
            DROPIN_HEADER,
            timeout,
            container=SSH_CLIENT_CONTAINER,
        )
    except RuntimeError as exc:
        return TaskResult(success=False, error=str(exc))
    if changed:
        _log("drop-in synced through augeas")

    if (changed or force) and cfg.directives:
        verify = _verify_effective_config(cfg.directives, timeout)
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
