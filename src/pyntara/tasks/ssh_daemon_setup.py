"""Task ssh_daemon_setup: install and configure the SSH server.

The task installs the configured SSH server package, runs its systemd
service and patches the daemon configuration through a drop-in file at
the configured sshd_config_dropin_path, never through sshd_config
itself: sshd_config is only checked for an Include directive that pulls
the drop-in directory in, because a missing Include means the drop-in
would be silently ignored. Directives are written through augeas
(augtool), which parses the real syntax and updates only what differs:
a directive that is already present with the same value is left
untouched, a directive with a different value is updated, a directive
that is no longer configured is removed, and the ownership comment is
guaranteed. An empty directives list removes the drop-in, so the task
can revoke its own settings. After a change the effective configuration
is verified with sshd -T, which prints the result of the whole Include
chain, so a directive overridden by another file or a keyword the
daemon does not know is reported as an error instead of being silently
accepted.

Ubuntu activates the daemon through the systemd socket unit
socket_unit_name, and the socket then owns the listen port: sshd_config
Port is ignored while the socket is enabled. The task disables the
socket, so the daemon listens on the port from the configuration; after
a start or restart the task verifies with ss that something listens on
the configured port.

The pre-generated key pair lives in task_data/ssh_daemon_setup/: the
private key is encrypted with a strong pass phrase, so it is committed
to the repository as is. The task copies both keys into the .ssh
directory of root and of every configured user and guarantees the
public key in authorized_keys without removing other keys, so
passwordless login works while the private key stays encrypted at rest.
A configured user that does not exist yet is skipped with a log line,
so the task stays idempotent.
The task owns the key files and the drop-in. The service is enabled
and started when inactive; a change that affects the port (a port
change or a socket disable) is applied with a restart, any other
change on an active service with a reload, which never drops existing
connections. The task is idempotent: it skips when the package is
installed, the Include is present, the drop-in matches through augeas,
the socket is disabled, the keys are in place and the service is
enabled and active; force mode rewrites the drop-in and restarts the
service but never reinstalls the package.
"""

from __future__ import annotations

import os
import pwd
import re
import subprocess
import time
from pathlib import Path

from pyntara.augeas import (
    apply_owner,
    ensure_augtool,
    include_covers_dropin,
    sync_dropin,
)
from pyntara.config import SshDaemonSetupConfig, SshDirective
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    APT_NONINTERACTIVE_ENV,
    install_package_once,
    package_is_installed,
    run_command,
    service_is_active,
    service_is_enabled,
)

# Module-level path constants are monkeypatched by the tests, which run
# against temporary fixtures instead of the real system (developer guide).
REPO_ROOT = Path(__file__).resolve().parents[3]
SSH_DATA_DIR = REPO_ROOT / "task_data" / "ssh_daemon_setup"

# The ownership comment of the drop-in, without the leading hash:
# augeas stores and writes comment values without it.
DROPIN_HEADER = "Managed by the Pyntara ssh_daemon_setup task."

# augeas lens for the sshd_config syntax.
SSHD_LENS = "Sshd.lns"


def _verify_effective_config(
    directives: tuple[SshDirective, ...], timeout: float
) -> str | None:
    """Error text when a configured directive is not effective; None when OK.

    sshd -T prints the effective configuration after every file of the
    Include chain is applied, so the check is independent of the version
    and of other files in the drop-in directory: a directive that a
    later file overrides, or a keyword the daemon does not know, is
    reported as an error, never silently accepted.
    """

    try:
        result = run_command(
            ["sshd", "-T"], check=False, capture=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cannot run sshd -T: {exc}"
    if result.returncode != 0:
        return f"sshd -T exited {result.returncode}: {result.stderr.strip()}"
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
                f"sshd -T reports {key} as {actual or 'unset'}, "
                f"expected {directive.value}"
            )
    return None


def _verify_listening_port(port: str, timeout: float) -> str | None:
    """Error text when nothing listens on the port; None when OK.

    The check runs after a start or restart, because the daemon binds
    the configured port only when the systemd socket is disabled: the
    socket owns the port otherwise.
    """

    try:
        result = run_command(
            ["ss", "-tlnp"], check=False, capture=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cannot run ss: {exc}"
    if result.returncode != 0:
        return f"ss -tlnp exited {result.returncode}"
    pattern = re.compile(rf":{re.escape(port)}(?:[ \t]|$)")
    if not any(pattern.search(line) for line in result.stdout.splitlines()):
        return f"no listener on port {port}"
    return None


def _write_bytes_if_different(
    path: Path, content: bytes, mode: int, uid: int, gid: int
) -> bool:
    """Write content with the given mode and owner; True when changed.

    The file is rewritten only when the content differs, so a repeated
    run leaves an untouched file alone. The task owns the key files,
    so a file with different content is overwritten: a manual edit
    cannot wedge the deployed keys.
    """

    if path.is_file() and path.read_bytes() == content:
        return False
    path.write_bytes(content)
    os.chmod(path, mode)
    apply_owner(path, uid, gid)
    return True


def _ensure_authorized_key(
    path: Path, key_line: str, mode: int, uid: int, gid: int
) -> bool:
    """Append the public key to authorized_keys; True when changed.

    The file is appended to, never rewritten, so keys the user added by
    hand survive. A key line already present is a no-op, so repeated
    runs do not accumulate duplicates.
    """

    existing: list[str] = []
    if path.is_file():
        existing = path.read_text(encoding="utf-8").splitlines()
    if key_line in existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(key_line + "\n")
    os.chmod(path, mode)
    apply_owner(path, uid, gid)
    return True


def _deploy_keys(
    ssh_dir: Path,
    private_bytes: bytes,
    public_bytes: bytes,
    public_line: str,
    pf_private_bytes: bytes,
    pf_public_bytes: bytes,
    pf_public_line: str,
    cfg: SshDaemonSetupConfig,
    uid: int,
    gid: int,
) -> bool:
    """Deploy the main and port-forwarding key pairs; True when changed.

    The .ssh directory is created with the configured mode and owned by
    the user, the private and public key files are written with their
    configured modes and the public key lines are guaranteed in
    authorized_keys. Both private keys stay encrypted, because they are
    copied as is from the repository; the port-forwarding public key
    line carries the configured restriction prefix, so that key can only
    open reverse tunnels.
    """

    changed = False
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, cfg.ssh_dir_mode)
    apply_owner(ssh_dir, uid, gid)
    if _write_bytes_if_different(
        ssh_dir / cfg.private_key_file_name,
        private_bytes,
        cfg.private_key_file_mode,
        uid,
        gid,
    ):
        changed = True
    if _write_bytes_if_different(
        ssh_dir / cfg.public_key_file_name,
        public_bytes,
        cfg.public_key_file_mode,
        uid,
        gid,
    ):
        changed = True
    if _ensure_authorized_key(
        ssh_dir / "authorized_keys",
        public_line,
        cfg.authorized_keys_file_mode,
        uid,
        gid,
    ):
        changed = True
    if _write_bytes_if_different(
        ssh_dir / cfg.port_forwarding_private_key_file_name,
        pf_private_bytes,
        cfg.private_key_file_mode,
        uid,
        gid,
    ):
        changed = True
    if _write_bytes_if_different(
        ssh_dir / cfg.port_forwarding_public_key_file_name,
        pf_public_bytes,
        cfg.public_key_file_mode,
        uid,
        gid,
    ):
        changed = True
    if _ensure_authorized_key(
        ssh_dir / "authorized_keys",
        pf_public_line,
        cfg.authorized_keys_file_mode,
        uid,
        gid,
    ):
        changed = True
    return changed


def _ensure_package(
    cfg: SshDaemonSetupConfig,
    timeout: float,
    skip_update: bool,
) -> tuple[bool, str]:
    """Install the SSH package; return (success, error_text).

    The apt index is refreshed once before the install, so dependencies
    resolve from a fresh index; skip_update=True disables the refresh
    for test or offline runs. Each attempt uses the shared
    noninteractive apt environment; total attempts are one initial plus
    retries.
    """

    if not skip_update:
        try:
            run_command(
                ["apt-get", "update"],
                extra_env=APT_NONINTERACTIVE_ENV,
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"apt index refresh: {exc}"
    ok = False
    error = ""
    for _ in range(cfg.install_retries + 1):
        ok, error = install_package_once(cfg.package_name, timeout)
        if ok:
            break
    return ok, error


def _wait_active(
    service_name: str,
    attempts: int,
    retry_delay_seconds: float,
    timeout: float,
) -> bool:
    """True when the service reports active within the readiness loop.

    The service may report activating for a moment after start, so the
    check is repeated with a pause until attempts run out.
    """

    for _ in range(attempts):
        time.sleep(retry_delay_seconds)
        if service_is_active(service_name, timeout):
            return True
    return False


def task(ctx: Context) -> TaskResult:
    """Install the SSH server, patch its config and deploy the keys.

    The goal is reached when the package is installed, sshd_config pulls
    the drop-in directory in, the drop-in matches the configured
    directives, the socket is disabled, the keys are in place for root
    and every existing configured user and the service is enabled and
    active; the task then returns changed=False. Otherwise it installs
    the package, syncs the drop-in through augeas, verifies the
    effective configuration with sshd -T, disables the socket, deploys
    the keys, enables the service and starts, reloads or restarts it.
    Every step is reported to stdout as single lines; any failure is
    returned as an error TaskResult, and the runner continues with the
    remaining tasks.
    """

    cfg = ctx.config.ssh_daemon_setup
    timeout = ctx.config.engine.command_timeout_seconds
    force = "ssh_daemon_setup" in ctx.force_tasks

    private_source = SSH_DATA_DIR / cfg.private_key_file_name
    public_source = SSH_DATA_DIR / cfg.public_key_file_name
    pf_private_source = SSH_DATA_DIR / cfg.port_forwarding_private_key_file_name
    pf_public_source = SSH_DATA_DIR / cfg.port_forwarding_public_key_file_name
    if not private_source.is_file() or not public_source.is_file():
        return TaskResult(
            success=False,
            error=(
                f"key files {cfg.private_key_file_name} and "
                f"{cfg.public_key_file_name} missing in {SSH_DATA_DIR}"
            ),
        )
    if not pf_private_source.is_file() or not pf_public_source.is_file():
        return TaskResult(
            success=False,
            error=(
                f"port-forwarding key files "
                f"{cfg.port_forwarding_private_key_file_name} and "
                f"{cfg.port_forwarding_public_key_file_name} "
                f"missing in {SSH_DATA_DIR}"
            ),
        )
    private_bytes = private_source.read_bytes()
    public_bytes = public_source.read_bytes()
    public_line = public_bytes.decode("utf-8").strip()
    pf_private_bytes = pf_private_source.read_bytes()
    pf_public_bytes = pf_public_source.read_bytes()
    pf_public_line = (
        f"{cfg.port_forwarding_authorized_keys_options} "
        f"{pf_public_bytes.decode('utf-8').strip()}"
    )

    changed = False

    installed = package_is_installed(
        cfg.package_name, cfg.package_status_timeout_seconds
    )
    _log(
        f"checking package {cfg.package_name}: "
        f"{'installed' if installed else 'missing'}"
    )
    if not installed:
        _log(f"installing package {cfg.package_name}")
        ok, error = _ensure_package(cfg, timeout, ctx.skip_apt_update)
        if not ok:
            return TaskResult(
                success=False,
                error=f"cannot install {cfg.package_name}: {error}",
            )
        _log("package installed")
        changed = True

    include_ok = include_covers_dropin(
        cfg.sshd_config_path, cfg.sshd_config_dropin_path
    )
    _log(
        f"checking Include directive in {cfg.sshd_config_path}: "
        f"{'found' if include_ok else 'missing'}"
    )
    if not include_ok:
        return TaskResult(
            success=False,
            error=(
                f"{cfg.sshd_config_path} has no Include directive covering "
                f"{cfg.sshd_config_dropin_path.parent}"
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
        return TaskResult(success=False, changed=changed, error=augtool_error)

    try:
        directives = tuple(
            (directive.name, directive.value)
            for directive in cfg.directives
        )
        dropin_changed, port_changed = sync_dropin(
            cfg.sshd_config_dropin_path,
            directives,
            cfg.dropin_file_mode,
            force,
            SSHD_LENS,
            DROPIN_HEADER,
            timeout,
            port_directive="Port",
        )
    except RuntimeError as exc:
        return TaskResult(success=False, changed=changed, error=str(exc))
    if dropin_changed:
        _log("drop-in synced through augeas")
        changed = True

    if (dropin_changed or force) and cfg.directives:
        verify = _verify_effective_config(cfg.directives, timeout)
        if verify is not None:
            return TaskResult(success=False, changed=changed, error=verify)
        _log("effective configuration verified through sshd -T")

    socket_enabled = service_is_enabled(cfg.socket_unit_name, timeout)
    socket_active = service_is_active(cfg.socket_unit_name, timeout)
    socket_needs_disable = socket_enabled or socket_active
    if socket_enabled:
        _log(f"checking socket {cfg.socket_unit_name}: enabled")
    elif socket_active:
        _log(f"checking socket {cfg.socket_unit_name}: active")
    else:
        _log(f"checking socket {cfg.socket_unit_name}: disabled")

    enabled = service_is_enabled(cfg.service_unit_name, timeout)
    active = service_is_active(cfg.service_unit_name, timeout)
    _log(
        f"checking autorun service {cfg.service_unit_name}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    _log(f"deploying keys into {cfg.root_ssh_dir}")
    if _deploy_keys(
        cfg.root_ssh_dir,
        private_bytes,
        public_bytes,
        public_line,
        pf_private_bytes,
        pf_public_bytes,
        pf_public_line,
        cfg,
        0,
        0,
    ):
        changed = True
    _log("root keys deployed")
    for user in cfg.users:
        try:
            record = pwd.getpwnam(user)
        except KeyError:
            _log(f"user {user} does not exist, skipping key deployment")
            continue
        ssh_dir = Path(record.pw_dir) / ".ssh"
        _log(f"deploying keys into {ssh_dir}")
        if _deploy_keys(
            ssh_dir,
            private_bytes,
            public_bytes,
            public_line,
            pf_private_bytes,
            pf_public_bytes,
            pf_public_line,
            cfg,
            record.pw_uid,
            record.pw_gid,
        ):
            changed = True
        _log("user keys deployed")

    if (
        not force
        and not changed
        and not socket_needs_disable
        and enabled
        and active
    ):
        _log("target state already reached, skipping")
        return TaskResult(success=True, changed=False, message="already configured")

    socket_changed = False
    if socket_needs_disable:
        _log(f"disabling socket: systemctl disable --now {cfg.socket_unit_name}")
        try:
            run_command(
                ["systemctl", "disable", "--now", cfg.socket_unit_name],
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl disable socket failed: {exc}",
            )
        _log("socket disabled")
        changed = True
        socket_changed = True

    if not enabled:
        _log(f"enabling service: systemctl enable {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "enable", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl enable failed: {exc}",
            )
        _log("service enabled")
        changed = True

    port_value = next(
        (
            directive.value
            for directive in cfg.directives
            if directive.name.casefold() == "port"
        ),
        None,
    )

    if not active:
        _log(f"starting service: systemctl start {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "start", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl start failed: {exc}",
            )
        _log("service started")
        if not _wait_active(
            cfg.service_unit_name,
            cfg.start_check_attempts,
            cfg.start_check_retry_delay_seconds,
            timeout,
        ):
            return TaskResult(
                success=False,
                changed=True,
                error=(
                    f"{cfg.service_unit_name} did not become active after "
                    f"{cfg.start_check_attempts} checks"
                ),
            )
        _log("service active")
        changed = True
        if port_value is not None:
            verify = _verify_listening_port(port_value, timeout)
            if verify is not None:
                return TaskResult(success=False, changed=changed, error=verify)
            _log(f"listener on port {port_value} verified")
    elif force or socket_changed or port_changed:
        _log(f"restarting service: systemctl restart {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "restart", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl restart failed: {exc}",
            )
        _log("service restarted")
        changed = True
        if port_value is not None:
            verify = _verify_listening_port(port_value, timeout)
            if verify is not None:
                return TaskResult(success=False, changed=changed, error=verify)
            _log(f"listener on port {port_value} verified")
    elif dropin_changed:
        _log(f"reloading service: systemctl reload {cfg.service_unit_name}")
        try:
            run_command(
                ["systemctl", "reload", cfg.service_unit_name], timeout=timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return TaskResult(
                success=False,
                changed=changed,
                error=f"systemctl reload failed: {exc}",
            )
        _log("service reloaded")
        changed = True

    return TaskResult(
        success=True,
        changed=changed,
        message=(
            f"SSH server {cfg.package_name} configured, service "
            f"{cfg.service_unit_name} active"
        ),
    )
