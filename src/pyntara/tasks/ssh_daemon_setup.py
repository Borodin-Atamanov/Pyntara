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
    ensure_augtool,
    include_covers_dropin,
    sync_dropin,
)
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import (
    apply_owner,
    install_package_once,
    package_is_installed,
    refresh_apt_index,
    run_command,
    service_is_active,
    service_is_enabled,
    substituted_command,
    task_data_dir,
)
from pyntara.values import engine as engine_values
from pyntara.values import ssh_daemon_setup as values
from pyntara.values.ssh_daemon_setup import SshDirective


def _verify_effective_config(
    directives: tuple[SshDirective, ...],
    timeout: float,
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
            list(values.EFFECTIVE_CONFIG_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
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


def _verify_listening_port(
    port: str, timeout: float
) -> str | None:
    """Error text when nothing listens on the port; None when OK.

    The check runs after a start or restart, because the daemon binds
    the configured port only when the systemd socket is disabled: the
    socket owns the port otherwise.
    """

    try:
        result = run_command(
            list(values.LISTENING_SOCKETS_COMMAND),
            check=False,
            capture=True,
            timeout=timeout,
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
    os.chmod(ssh_dir, values.SSH_DIR_MODE)
    apply_owner(ssh_dir, uid, gid)
    if _write_bytes_if_different(
        ssh_dir / values.PRIVATE_KEY_FILE_NAME,
        private_bytes,
        values.PRIVATE_KEY_FILE_MODE,
        uid,
        gid,
    ):
        changed = True
    if _write_bytes_if_different(
        ssh_dir / values.PUBLIC_KEY_FILE_NAME,
        public_bytes,
        values.PUBLIC_KEY_FILE_MODE,
        uid,
        gid,
    ):
        changed = True
    if _ensure_authorized_key(
        ssh_dir / "authorized_keys",
        public_line,
        values.AUTHORIZED_KEYS_FILE_MODE,
        uid,
        gid,
    ):
        changed = True
    if _write_bytes_if_different(
        ssh_dir / values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME,
        pf_private_bytes,
        values.PRIVATE_KEY_FILE_MODE,
        uid,
        gid,
    ):
        changed = True
    if _write_bytes_if_different(
        ssh_dir / values.PORT_FORWARDING_PUBLIC_KEY_FILE_NAME,
        pf_public_bytes,
        values.PUBLIC_KEY_FILE_MODE,
        uid,
        gid,
    ):
        changed = True
    if _ensure_authorized_key(
        ssh_dir / "authorized_keys",
        pf_public_line,
        values.AUTHORIZED_KEYS_FILE_MODE,
        uid,
        gid,
    ):
        changed = True
    return changed


def _ensure_package(
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
            refresh_apt_index(timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, f"apt index refresh: {exc}"
    ok = False
    error = ""
    for _ in range(values.INSTALL_RETRIES + 1):
        ok, error = install_package_once(values.PACKAGE_NAME, timeout)
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


def _result(*, changed: bool, message: str, warnings: list[str]) -> TaskResult:
    """Build the result of the task, carrying the warning of a skipped step."""

    if warnings:
        message = f"{message}; warnings: {'; '.join(warnings)}"
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )


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
    Every step is reported to stdout as single lines; a step that cannot
    run is reported as a warning of a completed task, the missing
    mechanism skips that step alone and every independent step still
    runs, so the runner continues with the remaining tasks.
    """

    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    owner_uid = engine_values.ROOT_OWNER_UID
    owner_gid = engine_values.ROOT_OWNER_GID
    force = ctx.task_name in ctx.force_tasks
    ssh_data_dir = task_data_dir(ctx.repo_root, ctx.task_name)
    warnings: list[str] = []

    private_source = ssh_data_dir / values.PRIVATE_KEY_FILE_NAME
    public_source = ssh_data_dir / values.PUBLIC_KEY_FILE_NAME
    pf_private_source = ssh_data_dir / values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME
    pf_public_source = ssh_data_dir / values.PORT_FORWARDING_PUBLIC_KEY_FILE_NAME
    keys_ready = private_source.is_file() and public_source.is_file()
    if not keys_ready:
        warnings.append(
            f"key files {values.PRIVATE_KEY_FILE_NAME} and "
            f"{values.PUBLIC_KEY_FILE_NAME} missing in {ssh_data_dir}"
        )
    pf_keys_ready = pf_private_source.is_file() and pf_public_source.is_file()
    if not pf_keys_ready:
        warnings.append(
            f"port-forwarding key files "
            f"{values.PORT_FORWARDING_PRIVATE_KEY_FILE_NAME} and "
            f"{values.PORT_FORWARDING_PUBLIC_KEY_FILE_NAME} "
            f"missing in {ssh_data_dir}"
        )
    private_bytes = private_source.read_bytes() if keys_ready else b""
    public_bytes = public_source.read_bytes() if keys_ready else b""
    public_line = public_bytes.decode("utf-8").strip()
    pf_private_bytes = pf_private_source.read_bytes() if pf_keys_ready else b""
    pf_public_bytes = pf_public_source.read_bytes() if pf_keys_ready else b""
    pf_public_line = (
        f"{values.PORT_FORWARDING_AUTHORIZED_KEYS_OPTIONS} "
        f"{pf_public_bytes.decode('utf-8').strip()}"
    )

    changed = False

    installed = package_is_installed(
        values.PACKAGE_NAME, values.PACKAGE_STATUS_TIMEOUT_SECONDS
    )
    _log(
        f"checking package {values.PACKAGE_NAME}: "
        f"{'installed' if installed else 'missing'}"
    )
    if not installed:
        _log(f"installing package {values.PACKAGE_NAME}")
        ok, error = _ensure_package(timeout, ctx.skip_apt_update)
        if ok:
            _log("package installed")
            changed = True
        else:
            warnings.append(f"cannot install {values.PACKAGE_NAME}: {error}")

    include_ok = include_covers_dropin(
        values.SSHD_CONFIG_PATH,
        values.SSHD_CONFIG_DROPIN_PATH,
        values.DROPIN_COMMENT_SIGN,
        values.INCLUDE_DIRECTIVE,
    )
    _log(
        f"checking Include directive in {values.SSHD_CONFIG_PATH}: "
        f"{'found' if include_ok else 'missing'}"
    )
    if not include_ok:
        # The drop-in is written anyway: the configured directives start
        # to work the moment the directive appears, so the work is not
        # thrown away by a line missing from a file we do not own.
        warnings.append(
            f"{values.SSHD_CONFIG_PATH} has no Include directive covering "
            f"{values.SSHD_CONFIG_DROPIN_PATH.parent}"
        )

    augtool_error = ensure_augtool(ctx, values.AUGEAS_TOOLS_PACKAGE_NAME)
    if augtool_error is not None:
        # Without augeas the drop-in cannot be written at all, so this
        # step alone is skipped and the rest of the task still runs.
        warnings.append(augtool_error)

    directives = tuple(
        (directive.name, directive.value) for directive in values.DIRECTIVES
    )
    dropin_changed = False
    port_changed = False
    if augtool_error is None:
        try:
            dropin_changed, port_changed = sync_dropin(
                values.SSHD_CONFIG_DROPIN_PATH,
                directives,
                values.DROPIN_FILE_MODE,
                force,
                values.AUGEAS_LENS,
                values.DROPIN_HEADER,
                timeout,
                values.DROPIN_COMMENT_SIGN,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
                port_directive=values.PORT_DIRECTIVE,
            )
        except RuntimeError as exc:
            warnings.append(str(exc))
        else:
            if dropin_changed:
                _log("drop-in synced through augeas")
                changed = True

    if (dropin_changed or force) and values.DIRECTIVES:
        verify = _verify_effective_config(values.DIRECTIVES, timeout)
        if verify is None:
            _log("effective configuration verified through sshd -T")
        else:
            warnings.append(verify)

    socket_enabled = service_is_enabled(values.SOCKET_UNIT_NAME, timeout)
    socket_active = service_is_active(values.SOCKET_UNIT_NAME, timeout)
    socket_needs_disable = socket_enabled or socket_active
    if socket_enabled:
        _log(f"checking socket {values.SOCKET_UNIT_NAME}: enabled")
    elif socket_active:
        _log(f"checking socket {values.SOCKET_UNIT_NAME}: active")
    else:
        _log(f"checking socket {values.SOCKET_UNIT_NAME}: disabled")

    enabled = service_is_enabled(values.SERVICE_UNIT_NAME, timeout)
    active = service_is_active(values.SERVICE_UNIT_NAME, timeout)
    _log(
        f"checking autorun service {values.SERVICE_UNIT_NAME}: "
        f"{'enabled' if enabled else 'disabled'}"
    )
    _log(f"checking service status: {'active' if active else 'inactive'}")

    keys_ready = keys_ready and pf_keys_ready
    if keys_ready:
        _log(f"deploying keys into {values.ROOT_SSH_DIR}")
        if _deploy_keys(
            values.ROOT_SSH_DIR,
            private_bytes,
            public_bytes,
            public_line,
            pf_private_bytes,
            pf_public_bytes,
            pf_public_line,
            0,
            0,
        ):
            changed = True
        _log("root keys deployed")
        for user in values.USERS:
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
                record.pw_uid,
                record.pw_gid,
            ):
                changed = True
            _log("user keys deployed")
    else:
        _log("skipping key deployment: a configured key file is missing")

    if not force and not changed and not socket_needs_disable and enabled and active:
        _log("target state already reached, skipping")
        return _result(changed=False, message="already configured", warnings=warnings)

    socket_changed = False
    if socket_needs_disable:
        _log(f"disabling socket: systemctl disable --now {values.SOCKET_UNIT_NAME}")
        try:
            run_command(
                substituted_command(
                    values.SOCKET_DISABLE_COMMAND,
                    {"socket_unit_name": values.SOCKET_UNIT_NAME},
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl disable socket failed: {exc}")
        else:
            _log("socket disabled")
            changed = True
            socket_changed = True

    if not enabled:
        _log(f"enabling service: systemctl enable {values.SERVICE_UNIT_NAME}")
        try:
            run_command(
                substituted_command(
                    values.SERVICE_ENABLE_COMMAND,
                    {"service_unit_name": values.SERVICE_UNIT_NAME},
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl enable failed: {exc}")
        else:
            _log("service enabled")
            changed = True

    port_value = next(
        (
            directive.value
            for directive in values.DIRECTIVES
            if directive.name.casefold() == values.PORT_DIRECTIVE.casefold()
        ),
        None,
    )

    if not active:
        _log(f"starting service: systemctl start {values.SERVICE_UNIT_NAME}")
        try:
            run_command(
                substituted_command(
                    values.SERVICE_START_COMMAND,
                    {"service_unit_name": values.SERVICE_UNIT_NAME},
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl start failed: {exc}")
        else:
            _log("service started")
            if not _wait_active(
                values.SERVICE_UNIT_NAME,
                values.START_CHECK_ATTEMPTS,
                values.START_CHECK_RETRY_DELAY_SECONDS,
                timeout,
            ):
                warnings.append(
                    f"{values.SERVICE_UNIT_NAME} did not become active after "
                    f"{values.START_CHECK_ATTEMPTS} checks"
                )
            else:
                _log("service active")
                changed = True
                if port_value is not None:
                    verify = _verify_listening_port(port_value, timeout)
                    if verify is None:
                        _log(f"listener on port {port_value} verified")
                    else:
                        warnings.append(verify)
    elif force or socket_changed or port_changed:
        _log(f"restarting service: systemctl restart {values.SERVICE_UNIT_NAME}")
        try:
            run_command(
                substituted_command(
                    values.SERVICE_RESTART_COMMAND,
                    {"service_unit_name": values.SERVICE_UNIT_NAME},
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl restart failed: {exc}")
        else:
            _log("service restarted")
            changed = True
            if port_value is not None:
                verify = _verify_listening_port(port_value, timeout)
                if verify is None:
                    _log(f"listener on port {port_value} verified")
                else:
                    warnings.append(verify)
    elif dropin_changed:
        _log(f"reloading service: systemctl reload {values.SERVICE_UNIT_NAME}")
        try:
            run_command(
                substituted_command(
                    values.SERVICE_RELOAD_COMMAND,
                    {"service_unit_name": values.SERVICE_UNIT_NAME},
                ),
                timeout=timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"systemctl reload failed: {exc}")
        else:
            _log("service reloaded")
            changed = True

    return _result(
        changed=changed,
        message=(
            f"SSH server {values.PACKAGE_NAME} configured, service "
            f"{values.SERVICE_UNIT_NAME} active"
        ),
        warnings=warnings,
    )
